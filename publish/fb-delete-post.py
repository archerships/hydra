#!/Users/crasch/av/venv/hydra/bin/python3
"""
fb-delete-post.py -- Delete a Facebook post via CDP.

Connects to an existing Chromium browser session via raw CDP WebSocket.

Chromium must be running with remote debugging:
  pkill -x 'Chromium'
  open -a 'Chromium' --args --remote-debugging-port=9226

Usage:
  fb-delete-post.py --url POST_URL [--yes] [--dry-run]
  fb-delete-post.py --post-id POST_ID [--yes] [--dry-run]
  fb-delete-post.py --latest [--yes] [--dry-run]

Flow (mirrors fb-edit-post.py):
  1. Navigate to the post (URL, post-id, or latest on profile)
  2. Open the "..." -> "Delete post" menu
  3. Confirm in the "Are you sure?" dialog (button labeled "Delete")
  4. Verify the post is gone (URL now 404 / profile no longer shows it)

Safety:
  --yes        Skip the interactive confirmation prompt
  --dry-run    Do everything except click Delete / confirm
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
# CDP wrapper (identical shape to fb-edit-post.py)
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

    async def key(self, key_name, code, vk):
        for t in ("keyDown", "keyUp"):
            await self.call("Input.dispatchKeyEvent",
                            {"type": t, "key": key_name, "code": code, "windowsVirtualKeyCode": vk})
            await asyncio.sleep(0.03)

    async def screenshot(self, name="fb-delete"):
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
            document.querySelectorAll('[style*="overflow"]').forEach(function(el) {
                if (el.scrollTop > 0) el.scrollTop = 0;
            });
        """)
        await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# Tab management
# ---------------------------------------------------------------------------

async def find_fb_tab():
    data = json.loads(urllib.request.urlopen(f"{CDP_URL}/json").read())
    tabs = [t for t in data if "facebook.com" in t.get("url", "") and t.get("type") == "page"]
    if not tabs:
        raise RuntimeError("No Facebook tab open in Chromium. Navigate to facebook.com first.")
    for t in tabs:
        if re.match(r"https://www\.facebook\.com/", t.get("url", "")):
            return t
    return tabs[0]


# ---------------------------------------------------------------------------
# Step 1: Navigate to the post
# ---------------------------------------------------------------------------

async def navigate_to_post(cdp: CDP, post_url: str | None,
                            post_id: str | None, latest: bool) -> str:
    if post_url:
        print(f"Navigating to post URL: {post_url[:60]}...")
        await cdp.navigate(post_url, wait=4.0)
        return post_url

    if post_id:
        url = f"https://www.facebook.com/permalink.php?story_fbid={post_id}&id=646125311"
        print(f"Navigating to post ID {post_id}...")
        await cdp.navigate(url, wait=3.5)
        return url

    print("Navigating to profile to find most recent post...")
    await cdp.navigate(FB_PROFILE_URL, wait=4.5)

    # Scroll down to trigger React post rendering
    for step in range(8):
        await cdp.js("window.scrollBy(0, 600)")
        await asyncio.sleep(1.2)
    await cdp.scroll_top()
    await asyncio.sleep(0.5)

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
# Step 2: Open the "..." menu and find "Delete post"
# ---------------------------------------------------------------------------

async def open_delete_dialog(cdp: CDP) -> bool:
    """Click the '...' menu on the post, select 'Delete post', and confirm.

    Returns True if the confirmation dialog is visible.
    """
    await cdp.scroll_top()

    # Find the '...' (more options) button. Same heuristics as fb-edit-post.py.
    more_btn = await cdp.js("""
        (function() {
            var OWN_NAMES = ['Archer T. Ships', 'Archer'];
            var candidates = [];

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

            for (var c of candidates) {
                for (var name of OWN_NAMES) {
                    if (c.label.includes(name)) {
                        return {x: Math.round(c.x), y: Math.round(c.y), label: c.label};
                    }
                }
            }

            candidates.sort(function(a, b) { return a.y - b.y; });
            var best = candidates[0];
            return {x: Math.round(best.x), y: Math.round(best.y), label: best.label};
        })()
    """)

    if not more_btn:
        await cdp.screenshot("fb-delete-no-more-btn")
        raise RuntimeError("Could not find '...' menu button on post. Check screenshot.")

    print(f"  Clicking '...' menu ({more_btn.get('label','?')}) at "
          f"({int(more_btn['x'])}, {int(more_btn['y'])})...")
    await cdp.mouse_click(more_btn["x"], more_btn["y"])
    await asyncio.sleep(1.5)

    # Click "Delete post" in the dropdown
    delete_item = await cdp.js("""
        (function() {
            var items = document.querySelectorAll('[role="menuitem"], [role="option"], li, a, span');
            for (var item of items) {
                var txt = item.textContent.trim();
                if (txt === 'Delete post' || txt === 'Delete Post') {
                    var rect = item.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {x: rect.left + rect.width/2, y: rect.top + rect.height/2};
                    }
                }
            }
            return null;
        })()
    """)

    if not delete_item:
        # Some Facebook surfaces use a nested menu: "Delete" inside a submenu
        delete_item = await cdp.js("""
            (function() {
                var items = document.querySelectorAll('[role="menuitem"], [role="option"], li, a, span');
                for (var item of items) {
                    var txt = item.textContent.trim();
                    if (txt === 'Delete' && item.closest('[role="menu"]')) {
                        var rect = item.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {x: rect.left + rect.width/2, y: rect.top + rect.height/2};
                        }
                    }
                }
                return null;
            })()
        """)

    if not delete_item:
        await cdp.screenshot("fb-delete-no-delete-option")
        raise RuntimeError("'Delete post' option not found in menu. Check screenshot.")

    print(f"  Clicking 'Delete post' at ({int(delete_item['x'])}, {int(delete_item['y'])})...")
    await cdp.mouse_click(delete_item["x"], delete_item["y"])
    await asyncio.sleep(2.5)

    # Confirmation dialog: look for a dialog with "Delete" as the primary button
    confirm = await cdp.js("""
        (function() {
            var dialogs = document.querySelectorAll('[role="dialog"]');
            for (var d of dialogs) {
                var text = d.innerText || d.textContent || '';
                if (text.includes('Delete') && text.includes('post')) {
                    var btns = d.querySelectorAll('[role="button"], button');
                    for (var b of btns) {
                        var label = (b.getAttribute('aria-label') || b.textContent || '').trim();
                        if (label === 'Delete' || label === 'Delete post') {
                            var rect = b.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                return {x: rect.left + rect.width/2,
                                        y: rect.top + rect.height/2,
                                        label: label};
                            }
                        }
                    }
                }
            }
            return null;
        })()
    """)

    if not confirm:
        # Facebook may show a generic confirmation dialog without 'post' in text
        confirm = await cdp.js("""
            (function() {
                var dialogs = document.querySelectorAll('[role="dialog"]');
                for (var d of dialogs) {
                    var btns = d.querySelectorAll('[role="button"], button');
                    for (var b of btns) {
                        var label = (b.getAttribute('aria-label') || b.textContent || '').trim();
                        if (label === 'Delete') {
                            var rect = b.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
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

    if not confirm:
        await cdp.screenshot("fb-delete-no-confirm-btn")
        print("  WARNING: Confirmation dialog not found. Check screenshot.")
        return False

    print(f"  Found '{confirm.get('label')}' confirm button at "
          f"({int(confirm['x'])}, {int(confirm['y'])})")
    return True


# ---------------------------------------------------------------------------
# Step 3: Confirm deletion
# ---------------------------------------------------------------------------

async def confirm_delete(cdp: CDP, dry_run: bool) -> bool:
    """Click the Delete button in the confirmation dialog. Returns True if clicked."""
    confirm = await cdp.js("""
        (function() {
            var dialogs = document.querySelectorAll('[role="dialog"]');
            for (var d of dialogs) {
                var text = d.innerText || d.textContent || '';
                if (text.includes('Delete')) {
                    var btns = d.querySelectorAll('[role="button"], button');
                    for (var b of btns) {
                        var label = (b.getAttribute('aria-label') || b.textContent || '').trim();
                        if (label === 'Delete' || label === 'Delete post') {
                            var rect = b.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                return {x: rect.left + rect.width/2,
                                        y: rect.top + rect.height/2,
                                        label: label};
                            }
                        }
                    }
                }
            }
            return null;
        })()
    """)

    if not confirm:
        return False

    if dry_run:
        print("  [dry-run] Delete button found but NOT clicked.")
        return True

    print(f"  Clicking '{confirm.get('label')}'...")
    await cdp.mouse_click(confirm["x"], confirm["y"])
    await asyncio.sleep(5.0)
    await cdp.screenshot("fb-delete-after-confirm")
    return True


# ---------------------------------------------------------------------------
# Step 4: Verify deletion
# ---------------------------------------------------------------------------

async def verify_deleted(cdp: CDP, post_url: str | None) -> bool:
    """Check that the post is no longer accessible."""
    if not post_url:
        return True  # nothing to check

    await cdp.navigate(post_url, wait=5.0)
    # Facebook shows "This content isn't available right now" or a login-wall for deleted posts
    page_text = await cdp.js("return document.body.innerText || '';")
    gone_markers = [
        "This content isn't available right now",
        "This content is no longer available",
        "Content isn't available",
        "the link you followed may be broken",
    ]
    if any(m.lower() in (page_text or "").lower() for m in gone_markers):
        print("  [VERIFY] Post confirmed deleted (content unavailable).")
        return True
    # Fallback: if the page has no post body / no "Actions for this post", assume gone
    has_actions = await cdp.js("""
        (function() {
            var btns = document.querySelectorAll('[role="button"]');
            for (var b of btns) {
                var label = b.getAttribute('aria-label') || '';
                if (label.toLowerCase().includes('actions for this post')) return true;
            }
            return false;
        })()
    """)
    if not has_actions:
        print("  [VERIFY] Post confirmed deleted (no post actions found).")
        return True
    print("  [WARN] Post may still be visible. Check screenshot.")
    await cdp.screenshot("fb-delete-verify-warn")
    return False


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

        post_url = await navigate_to_post(cdp, args.url, args.post_id, args.latest)
        await cdp.screenshot("fb-delete-landed")

        if not args.yes and not args.dry_run:
            print("")
            answer = input("Delete this post permanently? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                return
            print("")

        print("Opening Delete menu...")
        ok = await open_delete_dialog(cdp)
        if not ok:
            sys.exit("Delete confirmation dialog did not appear. Check screenshot.")
        print("  Delete dialog open.")

        clicked = await confirm_delete(cdp, dry_run=args.dry_run)
        if not clicked:
            await cdp.screenshot("fb-delete-confirm-fail")
            sys.exit("Could not click Delete button. Check screenshot.")

        if args.dry_run:
            print("[dry-run] Post NOT deleted.")
            return

        await verify_deleted(cdp, post_url)
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
                       help="Find and delete the most recent post on the profile")

    ap.add_argument("--yes", action="store_true",
                    help="Skip the interactive confirmation prompt")
    ap.add_argument("--dry-run", action="store_true",
                    help="Do everything except click Delete / confirm")

    args = ap.parse_args()

    if args.dry_run:
        print("[DRY RUN -- Delete will NOT be submitted]")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
