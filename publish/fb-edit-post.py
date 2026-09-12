#!/Users/crasch/av/venv/hydra/bin/python3
"""
fb-edit-post.py -- Edit a Facebook post and add a first comment.

Connects to an existing Chromium browser session via raw CDP WebSocket.

Chromium must be running with remote debugging:
  pkill -x 'Chromium'
  open -a 'Chromium' --args --remote-debugging-port=9226

Usage:
  fb-edit-post.py --url POST_URL --essay ESSAY_SLUG [options]
  fb-edit-post.py --post-id POST_ID --essay ESSAY_SLUG [options]
  fb-edit-post.py --latest  --essay ESSAY_SLUG [options]

Edit operations:
  --remove-links          Strip all bare URLs from post body
  --image PATH            Attach this image to the post (replaces link preview)
  --comment TEXT_FILE     Add the file's content as the first comment
  --dry-run               Do everything except click Save/Post

The script:
  1. Navigates to the post (by URL, post-id, or finds the latest on profile)
  2. Opens the "..." -> "Edit post" menu
  3. Edits body text as requested
  4. Attaches an image if provided
  5. Saves the edit
  6. Types and submits the comment
"""

import argparse
import asyncio
import json
import re
import os
import sys
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:
    sys.exit("websockets not found: pip install websockets")

CDP_URL        = os.environ.get("HYDRA_CDP_URL", "http://localhost:9226")
SCREENSHOT_DIR = Path.home() / "av" / "ast" / "img" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

FB_PROFILE_URL = "https://www.facebook.com/archerships/"


# ---------------------------------------------------------------------------
# CDP wrapper
# ---------------------------------------------------------------------------

class CDP:
    def __init__(self, ws):
        self._ws  = ws
        self._mid = 0

    async def call(self, method, params=None, timeout=20):
        self._mid += 1
        mid = self._mid
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                err = msg.get("error")
                if err:
                    raise RuntimeError(f"CDP error for {method}: {err}")
                return msg.get("result", {})

    async def js(self, expr, timeout=15):
        r = await self.call("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True, "awaitPromise": True},
                            timeout=timeout)
        exc = r.get("exceptionDetails")
        if exc:
            raise RuntimeError(f"JS exception: {exc.get('text', '?')}")
        return r.get("result", {}).get("value")

    async def mouse_click(self, x, y):
        for ev, button in [("mouseMoved", "none"), ("mousePressed", "left"), ("mouseReleased", "left")]:
            params = {"type": ev, "x": int(x), "y": int(y), "button": button}
            if button != "none":
                params["clickCount"] = 1
            await self.call("Input.dispatchMouseEvent", params)
            await asyncio.sleep(0.06)

    async def type_text(self, text):
        await self.call("Input.insertText", {"text": text})

    async def key(self, key_name, code, vk):
        for t in ("keyDown", "keyUp"):
            await self.call("Input.dispatchKeyEvent",
                            {"type": t, "key": key_name, "code": code, "windowsVirtualKeyCode": vk})
            await asyncio.sleep(0.03)

    async def screenshot(self, name="fb-edit"):
        import base64
        r   = await self.call("Page.captureScreenshot", {"format": "png"})
        b64 = r.get("data", "")
        if b64:
            path = SCREENSHOT_DIR / f"{name}.png"
            path.write_bytes(base64.b64decode(b64))
            print(f"  Screenshot: {path}")
        return r.get("data", "")

    async def navigate(self, url, wait=3.0):
        await self.call("Page.navigate", {"url": url}, timeout=60)
        await asyncio.sleep(wait)

    async def scroll_top(self):
        await self.js("""
            window.scrollTo(0, 0);
            // Also scroll any scrollable containers (Facebook permalink uses a div scroller)
            document.querySelectorAll('[style*="overflow"]').forEach(function(el) {
                if (el.scrollTop > 0) el.scrollTop = 0;
            });
        """)
        await asyncio.sleep(0.5)
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Tab management
# ---------------------------------------------------------------------------

async def find_fb_tab():
    data = json.loads(urllib.request.urlopen(f"{CDP_URL}/json").read())
    tabs = [t for t in data if "facebook.com" in t.get("url", "") and t.get("type") == "page"]
    if not tabs:
        raise RuntimeError("No Facebook tab open in Chromium. Navigate to facebook.com first.")
    # Prefer the tab on the actual domain (not fbsbx, etc.)
    for t in tabs:
        if re.match(r"https://www\.facebook\.com/", t.get("url", "")):
            return t
    return tabs[0]


# ---------------------------------------------------------------------------
# Helpers: strip URLs from text
# ---------------------------------------------------------------------------

URL_RE = re.compile(
    r"\bhttps?://[^\s\]\)\"'>]+",
    re.IGNORECASE,
)

def strip_links(text: str) -> str:
    """Remove all bare URL occurrences from text and collapse resulting blank lines."""
    cleaned = URL_RE.sub("", text)
    # Collapse 3+ newlines down to 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Step 1: Navigate to the post
# ---------------------------------------------------------------------------

async def navigate_to_post(cdp: CDP, post_url: str | None,
                            post_id: str | None, latest: bool) -> str:
    """Navigate to the target post. Returns the final URL."""
    if post_url:
        # If the edit dialog is already open, skip navigation to avoid "Leave page?" dialog
        edit_open = await cdp.js(FIND_EDIT_DIALOG_JS + " !== null")
        if edit_open:
            print("Edit dialog already open -- skipping navigation.")
        else:
            print(f"Navigating to post URL: {post_url[:60]}...")
            await cdp.navigate(post_url, wait=4.0)
        return post_url

    if post_id:
        url = f"https://www.facebook.com/permalink.php?story_fbid={post_id}&id=646125311"
        print(f"Navigating to post ID {post_id}...")
        await cdp.navigate(url, wait=3.5)
        return url

    # Find the most recent post on the profile page
    print("Navigating to profile to find most recent post...")
    await cdp.navigate(FB_PROFILE_URL, wait=4.5)

    # Scroll down to trigger React post rendering (Facebook lazy-loads posts)
    for step in range(8):
        await cdp.js("window.scrollBy(0, 600)")
        await asyncio.sleep(1.2)
    await cdp.scroll_top()
    await asyncio.sleep(0.5)

    # Get the first post's permalink
    permalink = await cdp.js("""
        (function() {
            var patterns = ['/posts/', 'story_fbid', 'pfbid', '/permalink/'];
            var links = document.querySelectorAll('a[href]');
            for (var a of links) {
                var href = a.href || '';
                for (var p of patterns) {
                    if (href.includes(p) && href.includes('facebook.com')) return href;
                }
            }
            return null;
        })()
    """)

    if permalink:
        print(f"Found most recent post: {permalink[:80]}")
        await cdp.navigate(permalink, wait=3.5)
        return permalink

    raise RuntimeError("Could not find a recent post on the profile page.")


# ---------------------------------------------------------------------------
# Step 2: Open Edit dialog
# ---------------------------------------------------------------------------

async def open_edit_dialog(cdp: CDP) -> bool:
    """Click the '...' menu on the post and select 'Edit post'."""
    # If the "Edit post" dialog is already open (e.g. from a previous dry run), skip.
    already_open = await cdp.js(FIND_EDIT_DIALOG_JS + " !== null")
    if already_open:
        print("  Edit dialog already open -- skipping open step.")
        return True

    await cdp.scroll_top()

    # Find the '...' (more options) button on OUR post.
    # Facebook aria-labels have the form "Actions for this post by AUTHOR NAME".
    # We look for our own name first; fall back to the topmost matching button.
    more_btn = await cdp.js("""
        (function() {
            var OWN_NAMES = ['Archer T. Ships', 'Archer'];
            var candidates = [];

            // Collect all small buttons whose aria-label contains "Actions for this post"
            var btns = document.querySelectorAll('[role="button"]');
            for (var btn of btns) {
                var label = btn.getAttribute('aria-label') || '';
                if (!label.toLowerCase().includes('actions for this post') &&
                    !label.toLowerCase().includes('post options')) continue;
                var rect = btn.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    candidates.push({
                        el: btn,
                        label: label,
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2
                    });
                }
            }

            if (candidates.length === 0) return null;

            // Prefer the one whose label mentions our own name
            for (var c of candidates) {
                for (var name of OWN_NAMES) {
                    if (c.label.includes(name)) {
                        return {x: Math.round(c.x), y: Math.round(c.y), label: c.label};
                    }
                }
            }

            // Else pick the one closest to the top of the viewport (our post is first)
            candidates.sort(function(a, b) { return a.y - b.y; });
            var best = candidates[0];
            return {x: Math.round(best.x), y: Math.round(best.y), label: best.label};
        })()
    """)

    if not more_btn:
        await cdp.screenshot("fb-edit-no-more-btn")
        raise RuntimeError("Could not find '...' menu button on post. Check screenshot.")

    print(f"  Clicking '...' menu ({more_btn.get('label','?')}) at "
          f"({int(more_btn['x'])}, {int(more_btn['y'])})...")
    await cdp.mouse_click(more_btn["x"], more_btn["y"])
    await asyncio.sleep(1.5)

    # Click "Edit post" in the dropdown
    edit_item = await cdp.js("""
        (function() {
            var items = document.querySelectorAll('[role="menuitem"], [role="option"], li, a');
            for (var item of items) {
                var txt = item.textContent.trim();
                if (txt === 'Edit post' || txt === 'Edit Post') {
                    var rect = item.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {x: rect.left + rect.width/2, y: rect.top + rect.height/2};
                    }
                }
            }
            return null;
        })()
    """)

    if not edit_item:
        await cdp.screenshot("fb-edit-no-edit-option")
        raise RuntimeError("'Edit post' option not found in menu. Check screenshot.")

    print(f"  Clicking 'Edit post' at ({int(edit_item['x'])}, {int(edit_item['y'])})...")
    await cdp.mouse_click(edit_item["x"], edit_item["y"])
    await asyncio.sleep(2.5)

    # Verify dialog opened by checking for the "Edit post" heading
    dialog_open = await cdp.js(FIND_EDIT_DIALOG_JS + " !== null")
    return bool(dialog_open)


# ---------------------------------------------------------------------------
# Step 3: Edit the post body
# ---------------------------------------------------------------------------

async def edit_body(cdp: CDP, new_text: str):
    """Replace the entire text content of the compose area with new_text."""
    # Find and click the contenteditable in the dialog
    coords = await cdp.js(_edit_dialog_js("""
        var ce = dialog.querySelector('[contenteditable="true"]') ||
                 dialog.querySelector('[role="textbox"]');
        if (!ce) return null;
        var rect = ce.getBoundingClientRect();
        return {x: rect.left + 10, y: rect.top + 10};
    """))

    if not coords:
        raise RuntimeError("Could not find text area in edit dialog.")

    print(f"  Clicking text area at ({int(coords['x'])}, {int(coords['y'])})...")
    await cdp.mouse_click(coords["x"], coords["y"])
    await asyncio.sleep(0.3)

    # Select all and delete
    await cdp.call("Input.dispatchKeyEvent",
                   {"type": "keyDown", "key": "a", "code": "KeyA",
                    "modifiers": 2, "windowsVirtualKeyCode": 65})
    await cdp.call("Input.dispatchKeyEvent",
                   {"type": "keyUp", "key": "a", "code": "KeyA",
                    "modifiers": 2, "windowsVirtualKeyCode": 65})
    await asyncio.sleep(0.2)
    await cdp.key("Delete", "Delete", 46)
    await asyncio.sleep(0.3)

    # Type new text
    print(f"  Typing new body ({len(new_text)} chars)...")
    await cdp.type_text(new_text)
    await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Step 3b: Remove the link preview card (optional)
# ---------------------------------------------------------------------------

async def remove_link_preview(cdp: CDP):
    """Click 'Remove link preview from your post' if present in the edit dialog.

    Uses JS .click() rather than mouse coordinates because the button is often
    partially outside the viewport (y > window.innerHeight), causing mouse clicks
    to miss even though the button is reachable via script.
    """
    label = await cdp.js("""
        (function() {
            var btns = document.querySelectorAll('[role="button"]');
            for (var b of btns) {
                var aria = b.getAttribute('aria-label') || '';
                if (aria === 'Remove link preview from your post' ||
                    aria === 'Remove All') {
                    b.click();
                    return aria;
                }
            }
            return null;
        })()
    """)
    if label:
        print(f"  Removed link preview (JS click: '{label}').")
        await asyncio.sleep(1.5)
    else:
        print("  No link preview button found -- skipping.")


# ---------------------------------------------------------------------------
# Step 4: Attach image
# ---------------------------------------------------------------------------

async def attach_image(cdp: CDP, image_path: Path):
    """
    Attach an image to the post being edited.

    Strategy: Facebook pre-renders hidden file inputs in the edit dialog.
    We inject directly via DOM.setFileInputFiles without clicking Photo/video,
    because clicking Photo/video in the edit dialog opens a separate "Create post"
    composer rather than adding to the edit.

    If direct injection fails (no pre-rendered input found), we fall back to
    clicking Photo/video with a guard that closes any "Create post" that opens.
    """
    if not image_path.exists():
        print(f"  WARNING: Image not found: {image_path}. Skipping.")
        return

    # Convert WebP to JPEG if needed
    img_to_use = image_path
    if image_path.suffix.lower() == ".webp":
        jpg_path = image_path.with_suffix(".jpg")
        if not jpg_path.exists():
            try:
                from PIL import Image
                im = Image.open(image_path).convert("RGB")
                im.save(jpg_path, "JPEG", quality=92)
                print(f"  Converted {image_path.name} -> {jpg_path.name}")
            except ImportError:
                print("  pillow not installed; trying to use webp directly.")
        if jpg_path.exists():
            img_to_use = jpg_path

    async def find_edit_dialog_file_input():
        """Return backendNodeId of the first file input inside the Edit post dialog."""
        # Get the nodeId of the edit dialog via JS (returns the element's outerHTML
        # length as a proxy -- we need the CDP nodeId differently)
        # Strategy: use DOM.getDocument + querySelectorAll scoped to the dialog.
        # We can't scope querySelectorAll to a specific nodeId that we found via JS,
        # so we use a JS trick: mark the dialog with a data attribute, then query it.
        marked = await cdp.js("""
            (function() {
                var dialogs = document.querySelectorAll('[role="dialog"]');
                for (var d of dialogs) {
                    var nodes = d.querySelectorAll('span, h1, h2, h3, [role="heading"]');
                    for (var n of nodes) {
                        if (n.children.length === 0 && n.textContent.trim() === 'Edit post') {
                            var inputs = d.querySelectorAll('input[type="file"]');
                            return inputs.length;
                        }
                    }
                }
                return 0;
            })()
        """)
        if not marked:
            return None

        # Mark the edit dialog's file inputs with a unique attribute so we can find
        # them via CDP's DOM.querySelectorAll on the full document.
        await cdp.js("""
            (function() {
                var dialogs = document.querySelectorAll('[role="dialog"]');
                for (var d of dialogs) {
                    var nodes = d.querySelectorAll('span, h1, h2, h3, [role="heading"]');
                    for (var n of nodes) {
                        if (n.children.length === 0 && n.textContent.trim() === 'Edit post') {
                            var inputs = d.querySelectorAll('input[type="file"]');
                            for (var i = 0; i < inputs.length; i++) {
                                inputs[i].setAttribute('data-editdialog', 'true');
                            }
                            return;
                        }
                    }
                }
            })()
        """)

        doc = await cdp.call("DOM.getDocument", {"depth": 0})
        root_id = doc["root"]["nodeId"]
        nodes = await cdp.call("DOM.querySelectorAll",
                               {"nodeId": root_id,
                                "selector": 'input[type="file"][data-editdialog="true"]'})
        node_ids = nodes.get("nodeIds", [])
        if not node_ids:
            # Fall back: any file input in the document
            nodes2 = await cdp.call("DOM.querySelectorAll",
                                    {"nodeId": root_id, "selector": 'input[type="file"]'})
            node_ids = nodes2.get("nodeIds", [])
        if node_ids:
            desc = await cdp.call("DOM.describeNode", {"nodeId": node_ids[0]})
            bnid = desc.get("node", {}).get("backendNodeId")
            print(f"  Found file input backendNodeId={bnid} "
                  f"({len(node_ids)} in dialog).")
            return bnid
        return None

    # ── Pass 1: try direct injection (no Photo/video click) ──────────────────
    await cdp.call("Page.enable")
    backend_node_id = None
    try:
        backend_node_id = await find_edit_dialog_file_input()
    except Exception as e:
        print(f"  DOM query (pass 1) failed: {e}")

    if backend_node_id is None:
        # ── Pass 2: click Photo/video, guard against Create post ─────────────
        print("  No pre-rendered file input; clicking Photo/video...")
        photo_btn = await cdp.js("""
            (function() {
                var dialog = document.querySelector('[role="dialog"]');
                if (!dialog) return null;
                var btn = dialog.querySelector('[aria-label="Photo/video"]') ||
                          dialog.querySelector('[aria-label="Add photo or video"]');
                if (!btn) {
                    var btns = dialog.querySelectorAll('[role="button"]');
                    for (var b of btns) {
                        var label = b.getAttribute('aria-label') || '';
                        if (label.toLowerCase().includes('photo')) { btn = b; break; }
                    }
                }
                if (!btn) return null;
                var rect = btn.getBoundingClientRect();
                return {x: rect.left + rect.width/2, y: rect.top + rect.height/2};
            })()
        """)
        if photo_btn:
            await cdp.mouse_click(photo_btn["x"], photo_btn["y"])
            await asyncio.sleep(1.5)
            # Close any "Create post" that opened
            wrong = await cdp.js("""
                (function() {
                    var dialogs = document.querySelectorAll('[role="dialog"]');
                    for (var d of dialogs) {
                        var nodes = d.querySelectorAll('span,h1,h2,h3,[role="heading"]');
                        for (var n of nodes)
                            if (n.children.length === 0 &&
                                n.textContent.trim() === 'Create post') return true;
                    }
                    return false;
                })()
            """)
            if wrong:
                print("  Create post opened — pressing Escape to close it.")
                await cdp.key("Escape", "Escape", 27)
                await asyncio.sleep(1.5)
        try:
            backend_node_id = await find_edit_dialog_file_input()
        except Exception as e:
            print(f"  DOM query (pass 2) failed: {e}")

    if backend_node_id:
        try:
            await cdp.call("DOM.setFileInputFiles",
                           {"backendNodeId": backend_node_id, "files": [str(img_to_use)]})
            print(f"  Injected: {img_to_use.name}")
        except Exception as e:
            print(f"  WARNING: DOM.setFileInputFiles failed: {e}")

        # Wait for the upload to be PROCESSED: FB renders an "Uploaded media
        #  Add more  Edit" block in the edit dialog once the file is attached.
        # Saving before this node appears loses the image (observed 2026-08-15:
        # file input set + Save clicked, post still text-only; the manual flow
        # shows "Uploaded media" appears ~2-3s after selection).
        confirmed = False
        for _ in range(15):
            await asyncio.sleep(1.0)
            ok = await cdp.js("""
                (function() {
                    var dialogs = document.querySelectorAll('[role="dialog"]');
                    for (var d of dialogs) {
                        var nodes = d.querySelectorAll('span, h1, h2, h3, [role="heading"]');
                        var isEdit = false;
                        for (var n of nodes) {
                            if (n.children.length === 0 && n.textContent.trim() === 'Edit post') {
                                isEdit = true; break;
                            }
                        }
                        if (isEdit && (d.innerText || '').includes('Uploaded media')) return true;
                    }
                    return false;
                })()
            """)
            if ok:
                confirmed = True
                print("  Upload confirmed (dialog shows 'Uploaded media').")
                break
        if not confirmed:
            print("  WARNING: 'Uploaded media' not seen after 15s -- image may not attach.")
            await cdp.screenshot("fb-edit-no-upload-confirm")
    else:
        print("  WARNING: Could not attach image (no file input found).")


# ---------------------------------------------------------------------------
# Step 5: Save the edit
# ---------------------------------------------------------------------------

FIND_EDIT_DIALOG_JS = """
    (function() {
        var dialogs = document.querySelectorAll('[role="dialog"]');
        for (var d of dialogs) {
            // Look for a span/heading with text "Edit post" inside the dialog
            var nodes = d.querySelectorAll('span, h1, h2, h3, [role="heading"]');
            for (var n of nodes) {
                if (n.children.length === 0 && n.textContent.trim() === 'Edit post') {
                    return d;
                }
            }
        }
        return null;
    })()
"""


def _edit_dialog_js(inner_expr):
    """Wrap JS to run inside the 'Edit post' dialog identified by its heading."""
    return f"""
    (function() {{
        var dialogs = document.querySelectorAll('[role="dialog"]');
        var dialog = null;
        for (var d of dialogs) {{
            var nodes = d.querySelectorAll('span, h1, h2, h3, [role="heading"]');
            for (var n of nodes) {{
                if (n.children.length === 0 && n.textContent.trim() === 'Edit post') {{
                    dialog = d;
                    break;
                }}
            }}
            if (dialog) break;
        }}
        if (!dialog) return null;
        {inner_expr}
    }})()
    """


FIND_SAVE_BUTTON_JS = _edit_dialog_js("""
        var labels = ['Save', 'Next', 'Done'];
        var btns = dialog.querySelectorAll('[role="button"], button');
        for (var label of labels) {
            for (var btn of btns) {
                var txt = btn.textContent.trim();
                var aria = btn.getAttribute('aria-label') || '';
                if (txt === label || aria === label) {
                    var rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0)
                        return {x: rect.left + rect.width/2,
                                y: rect.top + rect.height/2,
                                label: label};
                }
            }
        }
        return null;
""")


FIND_REMOVE_PREVIEW_JS = """
    (function() {
        var labels = ['Remove All', 'Remove link preview from your post'];
        var btns = document.querySelectorAll('[role="button"]');
        for (var label of labels) {
            for (var btn of btns) {
                var aria = btn.getAttribute('aria-label') || '';
                var txt = btn.textContent.trim();
                if (aria === label || txt === label) {
                    var rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0)
                        return {x: rect.left + rect.width/2,
                                y: rect.top + rect.height/2,
                                label: label};
                }
            }
        }
        return null;
    })()
"""


async def save_edit(cdp: CDP, dry_run: bool, remove_preview: bool = False):
    """
    Click Save in the edit dialog.
    Flow: [Next (link-preview config)] -> [Remove All (optional)] -> Save
    """
    btn = await cdp.js(FIND_SAVE_BUTTON_JS)
    if not btn:
        await cdp.screenshot("fb-edit-no-save-btn")
        raise RuntimeError("Save/Next button not found in edit dialog. Check screenshot.")

    print(f"  Found '{btn.get('label')}' at ({int(btn['x'])}, {int(btn['y'])})")

    if dry_run:
        print("  [dry-run] button found but NOT clicked.")
        return

    await cdp.mouse_click(btn["x"], btn["y"])
    print(f"  Clicked '{btn.get('label')}' (step 1).")
    await asyncio.sleep(3.0)
    await cdp.screenshot("fb-edit-after-next")

    if btn.get("label") != "Save":
        # After clicking Next, look for a Save/Done in ANY visible dialog —
        # not just "Edit post" — because the photo config screen may have a
        # different heading.  Try a broad search first, then the Edit-dialog-
        # scoped version as fallback.
        btn2_coords = await cdp.js("""
            (function() {
                var labels = ['Save', 'Done'];
                var dialogs = document.querySelectorAll('[role="dialog"]');
                // Prefer the topmost (last in DOM = highest z-index) dialog's button
                for (var i = dialogs.length - 1; i >= 0; i--) {
                    var d = dialogs[i];
                    var btns = d.querySelectorAll('[role="button"], button');
                    for (var label of labels) {
                        for (var b of btns) {
                            var txt = b.textContent.trim();
                            var aria = b.getAttribute('aria-label') || '';
                            if (txt === label || aria === label) {
                                var rect = b.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0)
                                    return {x: rect.left + rect.width/2,
                                            y: rect.top + rect.height/2,
                                            label: label};
                            }
                        }
                    }
                }
                return null;
            })()
        """)

        if not btn2_coords:
            # Fall back to edit-dialog scoped search
            btn2_coords = await cdp.js(FIND_SAVE_BUTTON_JS)

        if remove_preview:
            label = await cdp.js("""
                (function() {
                    var btns = document.querySelectorAll('[role="button"]');
                    for (var b of btns) {
                        var aria = b.getAttribute('aria-label') || '';
                        if (aria === 'Remove link preview from your post' || aria === 'Remove All') {
                            b.click();
                            return aria;
                        }
                    }
                    return null;
                })()
            """)
            if label:
                print(f"  Removed link preview on config screen (JS click: '{label}').")
                await asyncio.sleep(1.5)
            else:
                print("  No link preview remove button on config screen.")

        if btn2_coords:
            print(f"  Found '{btn2_coords.get('label')}' at "
                  f"({int(btn2_coords['x'])}, {int(btn2_coords['y'])})")
            await cdp.mouse_click(btn2_coords["x"], btn2_coords["y"])
            print(f"  Clicked '{btn2_coords.get('label')}' (step 2).")
            await asyncio.sleep(5.0)
            await cdp.screenshot("fb-edit-after-save")

            # If the edit dialog is still open with a Save button, one more click is needed
            btn3 = await cdp.js(FIND_SAVE_BUTTON_JS)
            if btn3 and btn3.get("label") == "Save":
                print(f"  Found final 'Save' at ({int(btn3['x'])}, {int(btn3['y'])})")
                await cdp.mouse_click(btn3["x"], btn3["y"])
                print("  Clicked final 'Save' (step 3).")
                await asyncio.sleep(8.0)
                await cdp.screenshot("fb-edit-after-final-save")
        else:
            print("  No second button found -- assuming save completed after Next.")
    else:
        await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# Step 6: Add a comment
# ---------------------------------------------------------------------------

async def add_comment(cdp: CDP, comment_text: str, dry_run: bool):
    """Find the comment box on the current post page and submit a comment."""
    await cdp.scroll_top()
    await asyncio.sleep(1.0)

    # Find the comment box -- look for "Write a comment..." placeholder
    comment_box = await cdp.js("""
        (function() {
            var selectors = [
                '[aria-label="Write a comment..."]',
                '[aria-label="Write a public comment..."]',
                '[aria-label="Leave a comment"]',
                '[placeholder*="comment"]',
            ];
            for (var sel of selectors) {
                var el = document.querySelector(sel);
                if (el) {
                    var rect = el.getBoundingClientRect();
                    if (rect.width > 0) return {x: rect.left + 10, y: rect.top + 10};
                }
            }
            // Fall back: any contenteditable near the bottom of the post
            var ces = document.querySelectorAll('[contenteditable="true"]');
            for (var ce of ces) {
                var rect = ce.getBoundingClientRect();
                var placeholder = ce.getAttribute('aria-placeholder') || ce.getAttribute('aria-label') || '';
                if (placeholder.toLowerCase().includes('comment')) {
                    return {x: rect.left + 10, y: rect.top + 10};
                }
            }
            return null;
        })()
    """)

    if not comment_box:
        # Scroll down a bit to reveal the comment box (it might be below the fold)
        await cdp.js("window.scrollBy(0, 400)")
        await asyncio.sleep(1.0)
        comment_box = await cdp.js("""
            (function() {
                var ces = document.querySelectorAll('[contenteditable="true"]');
                for (var ce of ces) {
                    var rect = ce.getBoundingClientRect();
                    var placeholder = ce.getAttribute('aria-placeholder') || ce.getAttribute('aria-label') || '';
                    if (placeholder.toLowerCase().includes('comment') && rect.width > 0) {
                        return {x: rect.left + 10, y: rect.top + 10};
                    }
                }
                return null;
            })()
        """)

    if not comment_box:
        await cdp.screenshot("fb-edit-no-comment-box")
        print("  WARNING: Comment box not found. Skipping comment. Check screenshot.")
        return

    print(f"  Clicking comment box at ({int(comment_box['x'])}, {int(comment_box['y'])})...")
    await cdp.mouse_click(comment_box["x"], comment_box["y"])
    await asyncio.sleep(0.5)

    print(f"  Typing comment ({len(comment_text)} chars)...")
    await cdp.type_text(comment_text)
    await asyncio.sleep(0.5)

    await cdp.screenshot("fb-edit-before-comment")

    if dry_run:
        print("  [dry-run] Comment typed but NOT submitted.")
        return

    # Submit: Ctrl+Enter or Enter (Facebook uses Enter to submit comments)
    await cdp.call("Input.dispatchKeyEvent",
                   {"type": "keyDown", "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13})
    await cdp.call("Input.dispatchKeyEvent",
                   {"type": "keyUp", "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13})
    await asyncio.sleep(2.0)
    print("  Comment submitted.")

    await cdp.screenshot("fb-edit-after-comment")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def run(args):
    tab    = await find_fb_tab()
    ws_url = tab["webSocketDebuggerUrl"]
    print(f"Connected to Chromium tab: {tab.get('url','')[:60]}")

    async with websockets.connect(ws_url, origin=None, open_timeout=10,
                                  max_size=10 * 1024 * 1024) as ws:
        cdp = CDP(ws)

        # ── Navigate to the post ──────────────────────────────────────────
        post_url = await navigate_to_post(cdp, args.url, args.post_id, args.latest)

        await cdp.screenshot("fb-edit-landed")

        # ── Open Edit dialog ──────────────────────────────────────────────
        print("Opening Edit post dialog...")
        ok = await open_edit_dialog(cdp)
        if not ok:
            await cdp.screenshot("fb-edit-dialog-fail")
            sys.exit("Edit dialog did not open. Check screenshot.")
        print("  Edit dialog open.")

        # ── Build new body text ───────────────────────────────────────────
        if args.remove_links:
            # Give dialog time to fully load its text content
            await asyncio.sleep(2.0)
            # Read current body from the contenteditable
            current_body = await cdp.js("""
                (function() {
                    var dialog = document.querySelector('[role="dialog"]');
                    if (!dialog) return '';
                    var ce = dialog.querySelector('[contenteditable="true"]') ||
                             dialog.querySelector('[role="textbox"]');
                    if (!ce) return '';
                    // innerText is unreliable in some FB editors; try textContent too
                    var text = ce.innerText || ce.textContent || '';
                    return text;
                })()
            """)
            print(f"  Current body ({len(current_body or '')} chars).")
            new_body = strip_links(current_body or "")
            print(f"  New body ({len(new_body)} chars, links removed).")
        elif args.body_file:
            new_body = Path(args.body_file).read_text(encoding="utf-8").strip()
        else:
            new_body = None  # no body change

        if new_body is not None:
            await edit_body(cdp, new_body)

        # ── Remove link preview card ──────────────────────────────────────
        if args.remove_links or args.image or args.remove_preview:
            print("Checking for link preview to remove...")
            await remove_link_preview(cdp)

        # ── Attach image ──────────────────────────────────────────────────
        if args.image:
            image_path = Path(args.image).resolve()
            print(f"Attaching image: {image_path.name}...")
            await attach_image(cdp, image_path)

        await cdp.screenshot("fb-edit-before-save")

        # ── Save ──────────────────────────────────────────────────────────
        print("Saving edit...")
        remove_preview = args.remove_links or args.remove_preview
        await save_edit(cdp, dry_run=args.dry_run, remove_preview=remove_preview)

        # ── Comment ───────────────────────────────────────────────────────
        if args.comment:
            comment_text = Path(args.comment).read_text(encoding="utf-8").strip()
            print(f"Adding comment ({len(comment_text)} chars)...")
            await add_comment(cdp, comment_text, dry_run=args.dry_run)

        # ── Verify: reload the post and count attached images ─────────────
        if args.image and not args.dry_run and post_url:
            print("Verifying post...")
            await cdp.navigate(post_url, wait=5.0)
            img_count = await cdp.js("""
                (function() {
                    var imgs = document.querySelectorAll('img[src]');
                    var count = 0;
                    for (var img of imgs) {
                        var src = img.src || '';
                        // fbcdn.net hosts all Facebook media; skip tiny avatars (<50px)
                        if (src.includes('fbcdn.net')) {
                            var rect = img.getBoundingClientRect();
                            if (rect.width >= 50 && rect.height >= 50) count++;
                        }
                    }
                    return count;
                })()
            """)
            if img_count and img_count > 0:
                print(f"  Post verified: {img_count} image(s) visible on page.")
            else:
                print("  WARNING: no images detected on post page after save.")
            await cdp.screenshot("fb-edit-verified")

        print("Done.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)

    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--url",     help="Direct URL to the Facebook post")
    group.add_argument("--post-id", help="Facebook post/story fbid")
    group.add_argument("--latest",  action="store_true",
                       help="Find and edit the most recent post on the profile")

    ap.add_argument("--remove-links", action="store_true",
                    help="Strip all bare URLs from the post body")
    ap.add_argument("--remove-preview", action="store_true",
                    help="Remove the link preview card during the Next->Save step")
    ap.add_argument("--body-file",
                    help="Replace post body with contents of this text file")
    ap.add_argument("--image",
                    help="Attach this image to the edited post")
    ap.add_argument("--comment",
                    help="Path to text file; submit its contents as the first comment")
    ap.add_argument("--dry-run", action="store_true",
                    help="Do everything except click Save/Comment submit")

    args = ap.parse_args()

    if args.dry_run:
        print("[DRY RUN -- Save and Comment will NOT be submitted]")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
