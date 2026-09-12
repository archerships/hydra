#!/Users/crasch/av/venv/hydra/bin/python3
"""
fb-publisher.py -- Post an essay to a Facebook personal profile via CDP.

Chromium must be running with remote debugging:
  pkill -x 'Chromium'
  open -a 'Chromium' --args --remote-debugging-port=9226

Usage:
  fb-publisher.py <text-file> [options]

Default behaviour:
  1. URLs are stripped from body text before posting (FB downranks linked posts).
  2. The first image referenced in the essay markdown is attached. Any additional
     image whose filename or EXIF contains "fbinclude" is also attached. Video
     URLs found in the original text get a yt-dlp thumbnail.
  3. After posting, a comment is added:
       [video links if any]
       [essay URL if --essay-url is given]
       [contact block from --contact-file]
"""

import argparse
import asyncio
import json
import re
import subprocess
import os
import sys
from pathlib import Path

try:
    import websockets
except ImportError:
    sys.exit("websockets not found: pip install websockets")

CDP_URL        = os.environ.get("HYDRA_CDP_URL", "http://localhost:9226")
FB_HOME_URL    = "https://www.facebook.com/"
FB_PROFILE_URL = "https://www.facebook.com/archerships/"

SCREENSHOT_DIR = Path.home() / "av" / "ast" / "img" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONTACT_FILE = (
    Path.home() / "av" / "prj" / "hydra" / "publish" / "config" / "fb-contact.txt"
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"youtube\.com/watch\?[^\s\])'\"]*|"
    r"youtu\.be/[^\s\])'\"]+|"
    r"vimeo\.com/[^\s\])'\"]+|"
    r"odysee\.com/[^\s\])'\"]+|"
    r"rumble\.com/[^\s\])'\"]+"
    r")",
    re.IGNORECASE,
)

URL_RE = re.compile(r"\bhttps?://[^\s\]\)\"'><]+", re.IGNORECASE)

MARKDOWN_IMG_RE = re.compile(r"!\[.*?\]\(([^){}\s]+)")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def strip_links(text: str) -> str:
    cleaned = URL_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_video_urls(text: str) -> list[str]:
    seen: set[str] = set()
    result = []
    for url in VIDEO_URL_RE.findall(text):
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


# ---------------------------------------------------------------------------
# Image discovery
# ---------------------------------------------------------------------------

def has_fbinclude(path: Path) -> bool:
    """True if the image filename or EXIF/info contains 'fbinclude'."""
    if "fbinclude" in path.name.lower():
        return True
    try:
        from PIL import Image
        img = Image.open(path)
        exif = img._getexif() if hasattr(img, "_getexif") else None
        if exif:
            for val in exif.values():
                if isinstance(val, bytes) and b"fbinclude" in val.lower():
                    return True
                if isinstance(val, str) and "fbinclude" in val.lower():
                    return True
        for val in img.info.values():
            txt = val.decode("utf-8", errors="ignore") if isinstance(val, bytes) else str(val)
            if "fbinclude" in txt.lower():
                return True
    except Exception:
        pass
    return False


def find_essay_images(essay_dir: Path, source_text: str) -> list[Path]:
    """
    Return images to attach, in order:
      1. The first image referenced in the essay markdown (or first
         alphabetically if no markdown is found).
      2. Any additional image with 'fbinclude' in its filename or EXIF,
         in the order they appear in the markdown (or alphabetically).
    """
    if not essay_dir or not essay_dir.is_dir():
        return []

    # Collect candidates from essay dir AND img/ subdirectory
    # (hero images live in SLUG/img/ since 2026-08-14)
    all_images = {
        p.name: p
        for p in list(essay_dir.iterdir()) + list((essay_dir / "img").iterdir())
        if p.suffix.lower() in IMAGE_EXTS
    }
    if not all_images:
        return []

    # Try to get order from a markdown file in the directory
    md_files = sorted(essay_dir.glob("*.md"))
    md_text = md_files[0].read_text(encoding="utf-8", errors="ignore") if md_files else ""

    # Build an ordered list of image paths as referenced in markdown
    ordered: list[Path] = []
    seen: set[str] = set()
    for m in MARKDOWN_IMG_RE.finditer(md_text or source_text):
        fname = Path(m.group(1)).name
        if fname in all_images and fname not in seen:
            ordered.append(all_images[fname])
            seen.add(fname)

    # Add any un-referenced images from the directory (for fbinclude check)
    for name, path in sorted(all_images.items()):
        if name not in seen:
            ordered.append(path)
            seen.add(name)

    if not ordered:
        ordered = sorted(all_images.values(), key=lambda p: p.name)

    first = ordered[0]
    result = [first]
    for img in ordered[1:]:
        if has_fbinclude(img):
            result.append(img)

    return result


# ---------------------------------------------------------------------------
# Video thumbnails
# ---------------------------------------------------------------------------

def download_video_thumbnail(video_url: str) -> Path | None:
    """Download a video thumbnail with yt-dlp. Returns a .jpg path or None."""
    tmp_dir = Path("/tmp/fb-publisher-thumbs")
    tmp_dir.mkdir(exist_ok=True)

    safe = re.sub(r"[^\w]", "_", video_url[-30:])
    out_tmpl = str(tmp_dir / f"thumb_{safe}.%(ext)s")

    r = subprocess.run(
        ["yt-dlp",
         "--write-thumbnail", "--skip-download",
         "--convert-thumbnails", "jpg",
         "--output", out_tmpl,
         video_url],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print(f"  WARNING: yt-dlp failed for {video_url[:60]}: {r.stderr[:200]}")
        return None
    candidates = list(tmp_dir.glob(f"thumb_{safe}*.jpg"))
    return candidates[0] if candidates else None


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
        r = await self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        exc = r.get("exceptionDetails")
        if exc:
            raise RuntimeError(f"JS exception: {exc.get('text', '?')}")
        return r.get("result", {}).get("value")

    async def mouse_click(self, x, y):
        for ev, button in [("mouseMoved", "none"), ("mousePressed", "left"), ("mouseReleased", "left")]:
            p = {"type": ev, "x": int(x), "y": int(y), "button": button}
            if button != "none":
                p["clickCount"] = 1
            await self.call("Input.dispatchMouseEvent", p)
            await asyncio.sleep(0.06)

    async def type_text(self, text):
        await self.call("Input.insertText", {"text": text})

    async def key(self, key_name, code, vk):
        for t in ("keyDown", "keyUp"):
            await self.call(
                "Input.dispatchKeyEvent",
                {"type": t, "key": key_name, "code": code, "windowsVirtualKeyCode": vk},
            )
            await asyncio.sleep(0.03)

    async def screenshot(self, name="fb-publisher"):
        import base64
        r   = await self.call("Page.captureScreenshot", {"format": "png"})
        b64 = r.get("data", "")
        if b64:
            path = SCREENSHOT_DIR / f"{name}.png"
            path.write_bytes(base64.b64decode(b64))
            print(f"  Screenshot: {path}")


# ---------------------------------------------------------------------------
# Tab lookup
# ---------------------------------------------------------------------------

async def find_fb_tab():
    import urllib.request
    data    = json.loads(urllib.request.urlopen(f"{CDP_URL}/json").read())
    fb_tabs = [t for t in data if "facebook.com" in t.get("url", "") and t.get("type") == "page"]
    if not fb_tabs:
        raise RuntimeError("No Facebook page open in Chromium. Navigate to facebook.com first.")
    for t in fb_tabs:
        if re.match(r"https://www\.facebook\.com/", t.get("url", "")):
            return t
    return fb_tabs[0]


# ---------------------------------------------------------------------------
# Compose dialog helpers
# ---------------------------------------------------------------------------

async def navigate_to_home(cdp: CDP):
    await cdp.call("Page.navigate", {"url": FB_HOME_URL}, timeout=60)
    await asyncio.sleep(3)


async def open_compose_dialog(cdp: CDP, retries=3) -> bool:
    await cdp.js("window.scrollTo(0, 0)")
    await asyncio.sleep(0.8)

    for attempt in range(retries):
        coords = await cdp.js("""
            (function() {
                var selectors = [
                    "[aria-label='Create a post']",
                    "[aria-label=\\"What's on your mind?\\"]",
                    "form [role='button']"
                ];
                for (var s of selectors) {
                    var els = document.querySelectorAll(s);
                    for (var el of els) {
                        var r = el.getBoundingClientRect();
                        if (r.width > 100 && r.height > 20)
                            return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
                    }
                }
                return null;
            })()
        """)
        if not coords:
            print(f"  Compose trigger not found (attempt {attempt+1}/{retries})")
            await asyncio.sleep(2)
            continue

        print(f"  Clicking compose trigger at ({coords['x']}, {coords['y']})...")
        await cdp.mouse_click(coords["x"], coords["y"])
        await asyncio.sleep(2.5)

        found = await cdp.js("""
            (function() {
                var dialogs = document.querySelectorAll('[role="dialog"]');
                for (var d of dialogs) {
                    if (d.querySelector('[contenteditable]')) return true;
                }
                return false;
            })()
        """)
        if found:
            print("  Compose dialog opened.")
            return True
        print(f"  Dialog did not appear (attempt {attempt+1}/{retries})")

    return False


async def focus_compose_box(cdp: CDP):
    coords = await cdp.js("""
        (function() {
            var dialogs = document.querySelectorAll('[role="dialog"]');
            for (var d of dialogs) {
                var ce = d.querySelector('[contenteditable="true"]') || d.querySelector('[role="textbox"]');
                if (ce) {
                    var r = ce.getBoundingClientRect();
                    if (r.width > 200) return {x: Math.round(r.left + 10), y: Math.round(r.top + 10)};
                }
            }
            var boxes = document.querySelectorAll('[contenteditable="true"]');
            for (var el of boxes) {
                var r = el.getBoundingClientRect();
                if (r.width > 300 && r.height > 30)
                    return {x: Math.round(r.left + 10), y: Math.round(r.top + 10)};
            }
            return null;
        })()
    """)
    if not coords:
        raise RuntimeError("Could not find compose text area.")
    print(f"  Clicking compose area at ({coords['x']}, {coords['y']})...")
    await cdp.mouse_click(coords["x"], coords["y"])
    await asyncio.sleep(0.5)
    return coords


# ---------------------------------------------------------------------------
# Image attachment (AppleScript for macOS native file dialog)
# ---------------------------------------------------------------------------

def _applescript_open_file(path: Path) -> bool:
    """Use AppleScript to navigate to a file path in an open macOS Open dialog."""
    path_str = str(path.resolve()).replace('"', '\\"')
    script = f'''
    tell application "System Events"
        tell process "Chromium"
            set frontmost to true
            delay 1.2
            keystroke "g" using {{command down, shift down}}
            delay 0.8
            keystroke "a" using command down
            delay 0.1
            set the clipboard to "{path_str}"
            keystroke "v" using command down
            delay 0.4
            key code 36
            delay 0.6
            key code 36
        end tell
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20)
    return result.returncode == 0


async def _click_photo_button(cdp: CDP, label_hint: str = "Photo/video") -> bool:
    """Click the Photo/video (or Add more) button in the compose area."""
    btn = await cdp.js(f"""
        (function() {{
            var hint = "{label_hint}".toLowerCase();
            var btns = document.querySelectorAll('[role="button"]');
            for (var b of btns) {{
                var label = (b.getAttribute('aria-label') || '').toLowerCase();
                if (label.includes('photo') || label === hint) {{
                    var r = b.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        return {{x: r.left + r.width/2, y: r.top + r.height/2, label: b.getAttribute('aria-label')}};
                }}
            }}
            return null;
        }})()
    """)
    if not btn:
        return False
    print(f"  Clicking '{btn['label']}' at ({int(btn['x'])}, {int(btn['y'])})...")
    await cdp.mouse_click(btn["x"], btn["y"])
    return True


async def attach_images(cdp: CDP, images: list[Path], dry_run: bool):
    """Attach images via the hidden <input type=file> (CDP DOM, no native dialog).

    Facebook compose dialogs expose a hidden file input; setting files on it
    directly avoids the macOS native file dialog + AppleScript path entirely
    (which requires Accessibility permission and is fragile against Chromium).
    """
    if not images:
        return
    if dry_run:
        print(f"  [dry-run] Would attach {len(images)} image(s): {[p.name for p in images]}")
        return

    await cdp.call("DOM.enable")
    doc = await cdp.call("DOM.getDocument")
    root_id = doc.get("root", {}).get("nodeId")

    # Prefer the file input inside the open compose dialog; fall back to any.
    file_input_node = None
    for selector in ['div[role="dialog"] input[type="file"]',
                     'input[type="file"]']:
        res = await cdp.call("DOM.querySelector",
                             {"nodeId": root_id, "selector": selector})
        nid = res.get("nodeId")
        if nid:
            file_input_node = nid
            print(f"  Found file input via selector: {selector}")
            break

    if not file_input_node:
        print("  WARNING: No <input type=file> found; cannot attach images.")
        return

    paths = [str(p.resolve()) for p in images]
    await cdp.call("DOM.setFileInputFiles",
                   {"nodeId": file_input_node, "files": paths}, timeout=30)
    print(f"  Attached {len(images)} image(s) via CDP file input: {[p.name for p in images]}")
    await asyncio.sleep(2.5)


# ---------------------------------------------------------------------------
# Link preview removal (JS click -- bypasses viewport clipping)
# ---------------------------------------------------------------------------

async def remove_link_preview(cdp: CDP):
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
        print(f"  Removed link preview ('{label}').")
        await asyncio.sleep(1.5)


# ---------------------------------------------------------------------------
# Post submission
# ---------------------------------------------------------------------------

FIND_POST_BUTTON_JS = """
    (function() {
        var labels = ['Post', 'Share now', 'Publish'];
        var dialog = document.querySelector('[role="dialog"]');
        var container = dialog || document;

        // Collect role=button candidates
        var btns = Array.from(container.querySelectorAll('[role="button"], button'));
        // Also gather any leaf text nodes matching the labels
        var allEls = Array.from(document.querySelectorAll('*'));
        for (var el of allEls) {
            if (el.children.length === 0 && labels.includes(el.textContent.trim()))
                btns.push(el);
        }

        for (var label of labels) {
            for (var btn of btns) {
                var txt = btn.textContent.trim();
                var aria = btn.getAttribute('aria-label') || '';
                if (txt === label || aria === label) {
                    var r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        return {x: Math.round(r.left + r.width/2),
                                y: Math.round(r.top + r.height/2),
                                label: label, w: r.width};
                }
            }
        }
        // Fallback: last visible button in dialog
        if (dialog) {
            var dbtns = dialog.querySelectorAll('[role="button"]');
            for (var i = dbtns.length - 1; i >= 0; i--) {
                var r = dbtns[i].getBoundingClientRect();
                if (r.width > 30 && r.height > 20)
                    return {x: Math.round(r.left + r.width/2),
                            y: Math.round(r.top + r.height/2),
                            label: '(last-dialog-button)', w: r.width};
            }
        }
        return null;
    })()
"""


async def click_post_button(cdp: CDP, dry_run: bool = False):
    """
    Drive Facebook's multi-step compose submit:
      Step 1 (optional): Next -- link-preview config
      Step 2: Post -- audience selector
      Step 3 (optional): Post -- Post settings intermediate dialog
    """
    # Step 1: optional Next (link-preview)
    next_coords = await cdp.js("""
        (function() {
            var dialog = document.querySelector('[role="dialog"]');
            if (!dialog) return null;
            var btns = dialog.querySelectorAll('[role="button"]');
            for (var btn of btns) {
                if (btn.textContent.trim() === 'Next' || btn.getAttribute('aria-label') === 'Next') {
                    var r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
                }
            }
            return null;
        })()
    """)
    if next_coords:
        print(f"  Found 'Next' (link-preview step) at ({next_coords['x']}, {next_coords['y']})")
        if dry_run:
            print("  [dry-run] Not clicked.")
            return
        await cdp.mouse_click(next_coords["x"], next_coords["y"])
        await asyncio.sleep(2.0)
        # Remove any link preview card that appeared on the config screen
        await remove_link_preview(cdp)

    # Step 2: Post / audience
    coords = await cdp.js(FIND_POST_BUTTON_JS)
    if not coords:
        raise RuntimeError("Could not find Post button in compose dialog.")

    print(f"  Found '{coords.get('label')}' at ({coords['x']}, {coords['y']})")
    if dry_run:
        print("  [dry-run] Not clicked.")
        return

    await cdp.mouse_click(coords["x"], coords["y"])
    print("  Post button clicked (step 2).")
    await asyncio.sleep(3.5)

    # Step 3: optional Post settings dialog
    settings_coords = await cdp.js(FIND_POST_BUTTON_JS)
    if settings_coords and settings_coords.get("label") in ("Post", "Share now", "Publish"):
        print(f"  Post settings dialog -- clicking '{settings_coords['label']}' "
              f"at ({settings_coords['x']}, {settings_coords['y']})")
        await cdp.mouse_click(settings_coords["x"], settings_coords["y"])
        print("  Step 3 clicked.")
        await asyncio.sleep(3.5)
    else:
        print("  No Post settings dialog -- post should be live.")


# ---------------------------------------------------------------------------
# Find the newly published post URL
# ---------------------------------------------------------------------------

async def find_new_post_url(cdp: CDP) -> str | None:
    """Return the URL of the post JUST created.

    Navigates to the profile and finds the newest post card -- the one whose
    timestamp says "Just now" / "now" / "few seconds ago" -- and extracts
    its permalink. Falls back to the topmost permalink if no fresh card is
    found (better than the old first-permalink grab, which hit pinned or
    older posts).
    """
    await cdp.call("Page.navigate", {"url": FB_PROFILE_URL}, timeout=60)
    await asyncio.sleep(5.0)

    for _ in range(4):
        await cdp.js("window.scrollBy(0, 600)")
        await asyncio.sleep(1.0)
    await cdp.js("window.scrollTo(0, 0)")
    await asyncio.sleep(1.0)

    fresh = await cdp.js("""
        (function() {
            var patterns = ['/posts/', 'story_fbid', 'pfbid', '/permalink/'];
            var cards = document.querySelectorAll('div[role="article"], div[data-pagelet]');
            for (var card of cards) {
                var text = (card.innerText || card.textContent || '').toLowerCase();
                var freshish = /just now|few seconds ago|now|\\d+[sm]\\b/.test(text);
                if (!freshish) continue;
                var links = card.querySelectorAll('a[href]');
                for (var a of links) {
                    var href = a.href || '';
                    for (var p of patterns) {
                        if (href.includes(p) && href.includes('facebook.com')) {
                            return href;
                        }
                    }
                }
            }
            return null;
        })()
    """)
    if fresh:
        return fresh

    # Fallback: topmost permalink
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
    return permalink


# ---------------------------------------------------------------------------
# Comment
# ---------------------------------------------------------------------------

def build_comment(
    essay_url: str | None,
    video_urls: list[str],
    contact_text: str,
) -> str:
    parts = []
    if video_urls:
        parts.append("\n".join(video_urls))
    if essay_url:
        parts.append(essay_url)
    if contact_text:
        parts.append(contact_text)
    return "\n\n".join(parts).strip()


async def add_comment(cdp: CDP, comment_text: str, dry_run: bool):
    await cdp.js("window.scrollTo(0, 0)")
    await asyncio.sleep(1.0)

    box = await cdp.js("""
        (function() {
            var ces = document.querySelectorAll('[contenteditable="true"]');
            for (var ce of ces) {
                var r = ce.getBoundingClientRect();
                var label = ce.getAttribute('aria-placeholder') || ce.getAttribute('aria-label') || '';
                if (label.toLowerCase().includes('comment') && r.width > 0)
                    return {x: r.left + 10, y: r.top + 10};
            }
            return null;
        })()
    """)
    if not box:
        await cdp.js("window.scrollBy(0, 400)")
        await asyncio.sleep(1.0)
        box = await cdp.js("""
            (function() {
                var ces = document.querySelectorAll('[contenteditable="true"]');
                for (var ce of ces) {
                    var r = ce.getBoundingClientRect();
                    var label = ce.getAttribute('aria-placeholder') || ce.getAttribute('aria-label') || '';
                    if (label.toLowerCase().includes('comment') && r.width > 0)
                        return {x: r.left + 10, y: r.top + 10};
                }
                return null;
            })()
        """)

    if not box:
        await cdp.screenshot("fb-publisher-no-comment-box")
        print("  WARNING: Comment box not found. Skipping comment.")
        return

    print(f"  Clicking comment box at ({int(box['x'])}, {int(box['y'])})...")
    await cdp.mouse_click(box["x"], box["y"])
    await asyncio.sleep(0.5)
    print(f"  Typing comment ({len(comment_text)} chars)...")
    await cdp.type_text(comment_text)
    await asyncio.sleep(0.5)
    await cdp.screenshot("fb-publisher-before-comment")

    if dry_run:
        print("  [dry-run] Comment typed but NOT submitted.")
        return

    await cdp.call("Input.dispatchKeyEvent",
                   {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    await cdp.call("Input.dispatchKeyEvent",
                   {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    await asyncio.sleep(3.0)
    print("  Comment submitted.")
    await cdp.screenshot("fb-publisher-after-comment")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

async def publish(args):
    # ── Read original text ────────────────────────────────────────────────
    text_path = Path(args.text_file)
    original_text = text_path.read_text(encoding="utf-8").strip()

    # ── Comment-only mode: skip body posting, go straight to comment ──
    if args.comment_only:
        print("[Comment-only mode]")
        comment_text = original_text  # text_file IS the comment
        video_urls = []
        images = []
        post_url = args.essay_url  # the "essay URL" IS the post URL
        post_text = ""
        # Skip body posting and go straight to browser + comment
        tab = await find_fb_tab()
        ws_url = tab["webSocketDebuggerUrl"]
        tab_url = tab.get("url", "")
        print(f"Connecting to Chromium tab: {tab_url[:60]}")
        async with websockets.connect(ws_url, origin=None, open_timeout=10,
                                      max_size=10 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            # Dismiss stale dialogs -- BUT NOT on the post page: the post
            # itself renders inside a role=dialog modal ("Archer T. Ships's
            # Post"), and dismissing it with Escape closes the post AND its
            # comment box (observed 2026-08-15: comment-only flow pressed
            # Escape on the post dialog, box vanished, comment failed).
            target = (args.essay_url or "").rstrip("/")
            cur_url = (tab.get("url") or "").rstrip("/")
            on_post = bool(target and (cur_url == target or cur_url.startswith(target + "?")))
            if not on_post:
                n_dialogs = await cdp.js("document.querySelectorAll('[role=\\\"dialog\\\"]').length")
                if n_dialogs:
                    print(f"  Dismissing {n_dialogs} stale dialog(s)...")
                    for _ in range(int(n_dialogs)):
                        for t in ("keyDown", "keyUp"):
                            await cdp.call("Input.dispatchKeyEvent",
                                           {"type": t, "key": "Escape", "code": "Escape",
                                            "windowsVirtualKeyCode": 27})
                        await asyncio.sleep(0.5)

            # Navigate to the post URL
            print(f"  Navigating to post URL...")

            # If the current tab already shows the target post, use it directly.
            # Creating a fresh tab via Target.createTarget often gets redirected
            # to the home feed (post page needs existing tab context), which is
            # why the comment box query failed on the new tab.
            cur_url = tab_url.rstrip("/")
            target = (post_url or "").rstrip("/")
            if target and (cur_url == target or cur_url.startswith(target + "?")):
                print("  Already on the post tab -- skipping new-tab creation.")
            else:
                # First open a new tab with just the post URL
                import urllib.request as _ur
                tabs_after = json.loads(_ur.urlopen(f"{CDP_URL}/json").read())
                existing_ids = {t["id"] for t in tabs_after}
                new_tab = await cdp.call("Target.createTarget",
                                         {"url": post_url, "newWindow": False})
                new_tab_id = new_tab.get("targetId", "")

                if new_tab_id:
                    # Wait for the new tab to load
                    import urllib.request as _ur
                    await asyncio.sleep(3)
                    for _ in range(10):
                        all_tabs = json.loads(_ur.urlopen(f"{CDP_URL}/json").read())
                        for t in all_tabs:
                            if t.get("id") == new_tab_id:
                                if "facebook.com" in t.get("url", ""):
                                    # Switch to this tab
                                    ws_url2 = t["webSocketDebuggerUrl"]
                                    # Reconnect to new tab
                                    ws2 = await websockets.connect(ws_url2, origin=None, open_timeout=10,
                                                                  max_size=10 * 1024 * 1024)
                                    cdp._ws = ws2  # swap ws
                                    print(f"  Switched to new tab: {t['url'][:60]}")
                                    break
                        else:
                            await asyncio.sleep(1)
                            continue
                        break

            await asyncio.sleep(5)
            # Now use the new tab to post the comment

            # Dismiss any leave-page dialogs -- same on_post guard: the post
            # view IS a dialog; Escape would close it and drop us on the feed.
            if not on_post:
                for _ in range(3):
                    n_dialogs = await cdp.js("document.querySelectorAll('[role=\\\"dialog\\\"]').length")
                    if int(n_dialogs) > 0:
                        for _ in range(int(n_dialogs)):
                            for t in ("keyDown", "keyUp"):
                                await cdp.call("Input.dispatchKeyEvent",
                                               {"type": t, "key": "Escape", "code": "Escape",
                                                "windowsVirtualKeyCode": 27})
                            await asyncio.sleep(0.5)

            # Add comment
            ok = await add_comment(cdp, comment_text, dry_run=args.dry_run)
            if ok:
                print("  Comment posted.")
            else:
                print("  [WARN] Comment may not have been submitted.")

        print("\n=== Summary ===")
        print(f"  Post: {post_url}")
        print(f"  Comment: {'POSTED' if ok else 'FAILED'}")
        return

    # ── Extract video URLs from original (before stripping) ───────────────
    video_urls = extract_video_urls(original_text) if not args.no_comment else []
    if video_urls:
        print(f"Found {len(video_urls)} video URL(s): {video_urls}")

    # ── Strip links from body ─────────────────────────────────────────────
    post_text = strip_links(original_text)
    print(f"Post body: {len(post_text)} chars (links stripped from {len(original_text)})")

    # ── Find images ───────────────────────────────────────────────────────
    images: list[Path] = []
    if not args.no_images:
        essay_dir = Path(args.essay_dir) if args.essay_dir else None
        if essay_dir:
            images = find_essay_images(essay_dir, original_text)
            print(f"Essay images to attach ({len(images)}): {[p.name for p in images]}")

        # Video thumbnails
        for vurl in video_urls:
            print(f"  Downloading thumbnail for: {vurl[:60]}...")
            thumb = download_video_thumbnail(vurl)
            if thumb:
                images.append(thumb)
                print(f"  Thumbnail: {thumb.name}")

    # ── Build comment ─────────────────────────────────────────────────────
    comment_text = ""
    if not args.no_comment:
        contact_file = Path(args.contact_file) if args.contact_file else DEFAULT_CONTACT_FILE
        contact_block = ""
        if contact_file.exists():
            contact_block = contact_file.read_text(encoding="utf-8").strip()
        else:
            print(f"  WARNING: Contact file not found: {contact_file}")

        comment_text = build_comment(args.essay_url, video_urls, contact_block)
        print(f"Comment: {len(comment_text)} chars")

    # ── Connect to browser ────────────────────────────────────────────────
    tab    = await find_fb_tab()
    ws_url = tab["webSocketDebuggerUrl"]
    tab_url = tab.get("url", "")
    print(f"Connecting to Chromium tab: {tab_url[:60]}")

    async with websockets.connect(ws_url, origin=None, open_timeout=10,
                                  max_size=10 * 1024 * 1024) as ws:
        cdp = CDP(ws)

        # Navigate to home if needed
        if not re.match(r"https://www\.facebook\.com/($|\?)", tab_url):
            print("Navigating to Facebook home...")
            await navigate_to_home(cdp)

        # Dismiss stale dialogs
        n_dialogs = await cdp.js("document.querySelectorAll('[role=\"dialog\"]').length")
        if n_dialogs:
            print(f"  Dismissing {n_dialogs} stale dialog(s)...")
            for _ in range(int(n_dialogs)):
                await cdp.call("Input.dispatchKeyEvent",
                               {"type": "keyDown", "key": "Escape", "code": "Escape",
                                "windowsVirtualKeyCode": 27})
                await cdp.call("Input.dispatchKeyEvent",
                               {"type": "keyUp", "key": "Escape", "code": "Escape",
                                "windowsVirtualKeyCode": 27})
                await asyncio.sleep(0.5)

        # Open compose dialog
        print("Opening compose dialog...")
        ok = await open_compose_dialog(cdp)
        if not ok:
            await cdp.screenshot("fb-publisher-compose-fail")
            sys.exit("Could not open compose dialog. Screenshot saved.")

        # Focus and type body
        print("Focusing compose text area...")
        await focus_compose_box(cdp)
        print(f"Typing post ({len(post_text)} chars)...")
        await cdp.type_text(post_text)
        await asyncio.sleep(0.5)

        # Remove any link preview Facebook auto-attached from cached URL
        await remove_link_preview(cdp)

        # Attach images
        if images:
            print(f"Attaching {len(images)} image(s)...")
            await attach_images(cdp, images, dry_run=args.dry_run)

        await cdp.screenshot("fb-publisher-before-post")

        # Submit
        print("Clicking Post...")
        await click_post_button(cdp, dry_run=args.dry_run)

        if args.dry_run:
            print("[dry-run] Post was NOT submitted.")
            return

        await cdp.screenshot("fb-publisher-after-post")

        add_comment_status = True
        post_url = None
        if comment_text:
            print("Finding new post to comment on...")
            post_url = await find_new_post_url(cdp)
            if post_url:
                print(f"  Post URL: {post_url}")
                await cdp.call("Page.navigate", {"url": post_url}, timeout=60)
                await asyncio.sleep(5.0)
            else:
                print("  WARNING: Could not find new post URL. Commenting on current page.")

            print("Adding comment to post...")
            await add_comment(cdp, comment_text, dry_run=False)

            # Verify comment contains the essay URL
            if args.essay_url and post_url:
                print("Verifying comment contains essay URL...")
                await asyncio.sleep(3)
                page_text = await cdp.js(f"""
                    (function() {{
                        return document.body.innerText || '';
                    }})()
                """)
                url_in_page = args.essay_url in (page_text or "")
                if url_in_page:
                    print(f"  [VERIFY] Essay URL found in post comments: {args.essay_url}")
                else:
                    print(f"  [WARN] Essay URL not visible on post page. The comment may not have been submitted.")
                    add_comment_status = False
            elif args.essay_url and not post_url:
                print(f"  [WARN] Could not find post URL -- skipping comment verification.")

        # Final summary
        print("")
        print("=== FB Publisher Summary ===")
        print(f"  Post: {'LIVE' if not args.dry_run else 'DRY RUN'}")
        print(f"  URL:  {post_url or 'unknown'}")
        if comment_text and not args.no_comment:
            print(f"  Comment: {'LIVE' if not args.dry_run and add_comment_status else 'SKIPPED/FAILED'}")
        print("Done.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("text_file", help="Essay body text (URLs stripped before posting)")
    ap.add_argument("--essay-dir", metavar="PATH",
                    help="Directory with essay images (markdown parsed for order)")
    ap.add_argument("--essay-url", metavar="URL",
                    help="archerships.com URL for this essay (added to comment)")
    ap.add_argument("--contact-file", metavar="FILE",
                    help=f"Contact block file (default: {DEFAULT_CONTACT_FILE})")
    ap.add_argument("--no-images", action="store_true",
                    help="Skip image attachment")
    ap.add_argument("--no-comment", action="store_true",
                    help="Skip adding comment after post")
    ap.add_argument("--dry-run", action="store_true",
                    help="Type everything but do NOT click Post or submit comment")
    ap.add_argument("--comment-only", action="store_true",
                    help="Skip posting body text; only post a comment to an existing post. Requires --essay-url.")
    args = ap.parse_args()

    if not Path(args.text_file).exists():
        sys.exit(f"Text file not found: {args.text_file}")

    if args.dry_run:
        print("[DRY RUN -- Post and Comment will NOT be submitted]")

    asyncio.run(publish(args))


if __name__ == "__main__":
    main()
