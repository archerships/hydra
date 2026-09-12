#!/Users/crasch/av/venv/hydra/bin/python3
"""
x-publisher.py
Publish or edit an archerships.com essay as a Twitter/X Article using Playwright
via Chromium's Chrome DevTools Protocol (CDP).

Usage:
  # Publish new article:
  python3 x-publisher.py <essay.html>
  python3 x-publisher.py <essay.html> --image /path/to/cover.jpg

  # Edit existing article by URL or ID:
  python3 x-publisher.py <essay.html> --edit https://x.com/i/article/12345
  python3 x-publisher.py <essay.html> --edit 12345

  # Auto-detect from essay frontmatter (reads/writes twitter id):
  python3 x-publisher.py --essay ~/av/doc/posts/SLUG/SLUG.html

  # Dry run (compose but do not publish):
  python3 x-publisher.py <essay.html> --dry-run

Requirements:
  pip install playwright beautifulsoup4
  Chromium must be installed (this script can auto-launch it).
  Playwright browser binaries are NOT required -- this uses the system Chromium
  via CDP (remote debugging).
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from essay_frontmatter import load_fm, get_platform_entry, set_platform_field, find_md_for_html
from x_publisher_toc import essay_to_plaintext, to_paste_html

sys.stdout.reconfigure(line_buffering=True)

SCREENSHOT_DIR    = os.path.expanduser("~/av/ast/img/screenshots")
CDP_URL           = os.environ.get("HYDRA_CDP_URL", "http://localhost:9226")
_CDP_MATCH        = re.search(r":(\d+)", CDP_URL)
CDP_PORT          = _CDP_MATCH.group(1) if _CDP_MATCH else "9226"
ARTICLES_LIST_URL = "https://x.com/compose/articles"


# ---------------------------------------------------------------------------
# HTML -> plain text extraction
# ---------------------------------------------------------------------------

def extract_essay(html_path: str) -> tuple[str, str]:
    """Return (title, body_markdown) from an archerships essay HTML file."""
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    # Title from <h1>
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else Path(html_path).stem

    # Body: everything inside <main>
    main = soup.find("main") or soup.find("body")
    if not main:
        raise ValueError("No <main> element found in essay HTML.")

    # Build list of visible child elements for lookahead / skip-ahead
    children = [el for el in main.children
                if hasattr(el, "name") and el.name is not None]

    lines = []
    skip = set()  # indices consumed by lookahead
    for idx, el in enumerate(children):
        if idx in skip:
            continue
        tag = el.name.lower()

        if tag == "figure":
            continue

        if tag == "p":
            text = el.get_text(" ", strip=True)
            if text:
                lines.append(text)
                lines.append("")

        elif tag == "h2":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"## {text}")
                lines.append("")

        elif tag == "h3":
            # Merge h3 + immediately following ul into one paragraph line.
            # This avoids H3 block margins in Twitter's article editor.
            heading = el.get_text(strip=True)
            if not heading:
                continue
            next_idx = idx + 1
            next_el = children[next_idx] if next_idx < len(children) else None
            if next_el is not None and next_el.name.lower() == "ul":
                items = []
                for li in next_el.find_all("li", recursive=False):
                    t = re.sub(r"  +", " ", next_el.get_text(" ", strip=True))
                    t = re.sub(r"  +", " ", li.get_text(" ", strip=True))
                    items.append(t)
                lines.append(f"**{heading}** | " + " | ".join(items))
                skip.add(next_idx)
            else:
                lines.append(f"### {heading}")
                lines.append("")

        elif tag == "ul":
            items = []
            for li in el.find_all("li", recursive=False):
                t = re.sub(r"  +", " ", li.get_text(" ", strip=True))
                items.append(t)
            lines.append(" | ".join(items))
            next_tag = children[idx+1].name.lower() if idx+1 < len(children) else None
            if next_tag not in ("h2", "h3"):
                lines.append("")

        elif tag == "ol":
            for i, li in enumerate(el.find_all("li", recursive=False), 1):
                t = re.sub(r"  +", " ", li.get_text(" ", strip=True))
                lines.append(f"{i}. {t}")
            lines.append("")

        elif tag == "div":
            table_url   = el.get("data-table-url", "").strip()
            table_title = el.get("data-table-title", "").strip()
            if table_url:
                label = table_title if table_title else table_url
                lines.append(f"**{label}** -- {table_url}")
                lines.append("")

    body = "\n".join(lines).strip()
    return title, body


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------

def screenshot(page, name: str) -> None:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"tw-article-{name}.png")
    page.screenshot(path=path)
    print(f"  screenshot: {path}")


def detect_cover_image(html_path: str) -> str | None:
    """Return absolute path to the first local <img> in <main>, or None."""
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")
    main = soup.find("main")
    if not main:
        return None
    img = main.find("img")
    if not img or not img.get("src"):
        return None
    src = img["src"]
    if src.startswith("http"):
        return None
    candidate = (Path(html_path).parent / src).resolve()
    return str(candidate) if candidate.exists() else None


def detect_table_url(html_path: str) -> str | None:
    """Return the data-table-url value from .recs-wrap, or None."""
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")
    wrap = soup.find("div", class_="recs-wrap")
    if not wrap:
        return None
    return wrap.get("data-table-url", "").strip() or None


def detect_table_preview_image(html_path: str) -> str | None:
    """Find the *-table-preview-*.png in the same directory as the HTML file."""
    html_dir = Path(html_path).parent
    stem = Path(html_path).stem
    slug = re.sub(r'^\d{4}-\d{2}-\d{2}-', '', stem)
    candidates = list(html_dir.glob(f"{slug}-table-preview-*.png"))
    if not candidates:
        candidates = list(html_dir.glob("*-table-preview-*.png"))
    return str(candidates[0]) if candidates else None


def prepare_preview_image(image_path: str, top_crop: int = 80) -> str:
    """Return a cropped copy of the preview image with the top N pixels removed.

    The preview screenshot includes a page breadcrumb and heading at the top
    that aren't useful in the article body. Crop them off and return a temp
    path so the original file is unchanged.
    """
    try:
        from PIL import Image
    except ImportError:
        print("  [WARN] Pillow not installed -- using original preview image without crop")
        return image_path

    img = Image.open(image_path)
    w, h = img.size
    if top_crop >= h:
        return image_path

    cropped = img.crop((0, top_crop, w, h))
    tmp = tempfile.NamedTemporaryFile(
        suffix=".png", dir=os.path.expanduser("~/av/tmp"), delete=False
    )
    tmp.close()
    cropped.save(tmp.name)
    print(f"  Preview cropped: top {top_crop}px removed -> {tmp.name}")
    return tmp.name


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode(), check=True)


def copy_html_to_clipboard(page, html_text: str, plain_text: str) -> bool:
    """Put BOTH text/html and text/plain on the clipboard via the page's
    Clipboard API (the composer honors the HTML form: <br> soft returns
    stay inside ONE block instead of every newline becoming a paragraph
    block). Falls back to plain pbcopy when the API is unavailable."""
    try:
        ok = page.evaluate("""async (payload) => {
            try {
                await navigator.clipboard.write([new ClipboardItem({
                    'text/html': new Blob([payload.html], {type: 'text/html'}),
                    'text/plain': new Blob([payload.plain], {type: 'text/plain'})
                })]);
                return true;
            } catch (e) { return false; }
        }""", {"html": html_text, "plain": plain_text})
    except Exception:
        ok = False
    if not ok:
        copy_to_clipboard(plain_text)
    return ok


def paste(page) -> None:
    page.keyboard.press("Meta+v")
    page.wait_for_timeout(800)


def log_page_count(context, step: str) -> None:
    """Log how many browser tabs are open after a publishing step."""
    n = len(context.pages)
    print(f"[PAGES] {step}: {n} tab(s) open")


def paste_image_clipboard(page, image_path: str) -> bool:
    """Put a PNG on the macOS clipboard and paste it into the focused editor.

    Does NOT open any dialog (Insert > Media always times out and scrambles
    the cursor position when dismissed). The caller must ensure the cursor is
    already at the correct position in the body field.
    """
    print(f"  Pasting image from clipboard: {Path(image_path).name}")
    try:
        subprocess.run(
            ['osascript', '-e',
             f'set the clipboard to (read file POSIX file "{image_path}" as \xabclass PNGf\xbb)'],
            check=True
        )
    except Exception as e:
        print(f"    osascript failed: {e}")
        return False
    page.wait_for_timeout(300)
    page.keyboard.press("Meta+v")
    page.wait_for_timeout(2500)
    screenshot(page, "body-image-pasted")
    print("    Image pasted.")
    return True


def append_table_link(page, table_url: str) -> None:
    """Move cursor to end of body field and append the solo table URL."""
    body_field = page.locator('div[role="textbox"][contenteditable="true"]').first
    body_field.click()
    page.keyboard.press("Meta+End")
    page.wait_for_timeout(200)
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    copy_to_clipboard(f"Full table: {table_url}")
    paste(page)


def insert_image_via_menu(page, image_path: str) -> bool:
    """Click Insert > Media in the article toolbar to upload image at cursor position.
    The body field must be focused before calling this."""
    print(f"  Inserting image via Insert menu: {Path(image_path).name}")
    screenshot(page, "img-1-pre-insert")

    # Find Insert toolbar button (tries multiple selectors)
    insert_btn = None
    for sel in [
        'button:has-text("Insert")',
        '[role="button"]:has-text("Insert")',
        '[aria-label="Insert"]',
    ]:
        try:
            candidate = page.locator(sel).first
            candidate.wait_for(state="visible", timeout=2000)
            insert_btn = candidate
            break
        except Exception:
            continue
    if insert_btn is None:
        print("    Insert button not found -- dumping toolbar for diagnosis:")
        btns = page.evaluate('''() => {
            return [...document.querySelectorAll("button,[role=button]")]
                .map(b => ({label: b.getAttribute("aria-label"), text: b.innerText.trim().substring(0,30), top: b.getBoundingClientRect().top}))
                .filter(b => b.top >= 0 && b.top < 80 && (b.label || b.text));
        }''')
        for b in btns:
            print(f"      {b}")
        return False

    insert_btn.click()
    page.wait_for_timeout(600)
    screenshot(page, "img-2-insert-open")

    # Click Media in the dropdown
    try:
        media_item = page.get_by_text("Media", exact=True).first
        media_item.wait_for(state="visible", timeout=3000)
        media_item.click()
        page.wait_for_timeout(800)
        screenshot(page, "img-3-media-dialog")
    except Exception as e:
        print(f"    Media menu item not found: {e}")
        return False

    # The Insert dialog is now open with a file drop zone.
    # Strategy 1: use expect_file_chooser while clicking the drop-zone.
    # Strategy 2: set_input_files on the file input INSIDE the dialog.
    # We must never touch the cover-image input (input[type="file"]:first-of-type).
    page.wait_for_timeout(800)  # let dialog fully render

    file_set = False

    # Strategy 1 — try multiple click targets within the dialog to trigger a file chooser
    dialog = page.locator('[role="dialog"]').last
    box = dialog.bounding_box() or {}
    click_targets = []
    if box:
        cx = box["x"] + box["width"] / 2
        # upper-centre (camera icon area), mid-centre, lower-centre
        for frac in (0.3, 0.5, 0.65):
            click_targets.append((cx, box["y"] + box["height"] * frac))

    for (cx, cy) in click_targets:
        try:
            with page.expect_file_chooser(timeout=3000) as fc_ctx:
                page.mouse.click(cx, cy)
            fc_ctx.value.set_files(image_path)
            print("    File submitted via file chooser.")
            file_set = True
            break
        except Exception:
            pass

    if not file_set:
        # Strategy 2 — close the Insert dialog and paste image from clipboard.
        # The dialog has no accessible file input; pasting is the reliable fallback.
        print("    File chooser timed out -- closing dialog, pasting from clipboard.")
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
        try:
            subprocess.run(
                ['osascript', '-e',
                 f'set the clipboard to (read file POSIX file "{image_path}" as \xabclass PNGf\xbb)'],
                check=True
            )
            print("    Image placed on macOS clipboard.")
        except Exception as e2:
            print(f"    osascript failed: {e2}")
            return False
        body_field = page.locator('div[role="textbox"][contenteditable="true"]').first
        body_field.focus()  # refocus without repositioning the cursor
        page.wait_for_timeout(300)
        page.keyboard.press("Meta+v")
        page.wait_for_timeout(2500)
        print("    Pasted image from clipboard into body.")
        file_set = True
        # Clipboard paste doesn't trigger a crop dialog, so skip that block
        screenshot(page, "img-4-pasted")
        return True

    page.wait_for_timeout(3000)
    screenshot(page, "img-4-uploaded")

    # Dismiss the Edit media crop dialog (Apply button), then wait for mask to clear
    for attempt in range(3):
        mask_present = page.evaluate(
            '''() => !!document.querySelector("[data-testid='mask']")''')
        if not mask_present:
            break
        # Try Apply
        try:
            apply_btn = page.locator('button:has-text("Apply"), [role="button"]:has-text("Apply")').first
            apply_btn.wait_for(state="visible", timeout=3000)
            apply_btn.click(force=True)
            page.wait_for_timeout(1500)
            screenshot(page, f"img-5-apply-{attempt}")
            print(f"    Apply clicked (attempt {attempt + 1}).")
        except Exception:
            # Try pressing Escape to close any remaining dialog
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

    # Final mask check
    mask_still = page.evaluate('''() => !!document.querySelector("[data-testid='mask']")''')
    if mask_still:
        print("    Warning: mask overlay still present after image insert.")
    else:
        print("    Image inserted, dialogs cleared.")
    return True


def dump_body_blocks(page, label: str = "body-blocks") -> None:
    """Print the composer's block tree (one line per [data-block]) -- the
    authoritative check that segments did not fuse into one block."""
    blocks = page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('[data-block]').forEach((el, i) => {
            const t = (el.innerText || '').trim();
            if (t) out.push({i, tag: el.tagName.toLowerCase(), text: t.slice(0, 80)});
        });
        return out;
    }""")
    print(f"  [{label}] {len(blocks)} non-empty blocks")
    for b in blocks:
        print(f"    {b['i']:>3} {b['tag']:<4} {b['text']}")


def compose_body_segments(page, context, html_path: str, dry_run: bool = False) -> None:
    """Compose the article body per the Character-Based Structural
    Convention: convert the essay HTML to one plaintext document (decimal
    TOC, numbered headings, reflowed body, "---" + "[N] Citation
    footnotes) and paste it in ONE operation. A single paste eliminates
    per-segment fusion and minimizes clipboard/focus races with other
    apps (per-segment pasting was both slow and fragile)."""
    body_field = page.locator('div[role="textbox"][contenteditable="true"]').first
    body_field.wait_for(state="visible", timeout=10000)
    plain = essay_to_plaintext(html_path)
    if not plain:
        print("  [WARN] no plaintext content extracted from essay HTML")
        return
    body_field.click()
    page.wait_for_timeout(300)
    paste_html = to_paste_html(plain)
    used_html = copy_html_to_clipboard(page, paste_html, plain)
    paste(page)
    page.wait_for_timeout(2500)
    mode = "HTML paste (<br> soft returns)" if used_html else "plaintext paste"
    print(f"  Body composed: single {mode} ({len(paste_html)} html chars / {len(plain)} plain chars)")


# ---------------------------------------------------------------------------
# Twitter Articles composer
# ---------------------------------------------------------------------------

def publish_article(page, context, html_path: str, title: str, body: str,
                    image_path: str = None, body_image_path: str = None,
                    table_url: str = None, dry_run: bool = False) -> None:
    print(f"Navigating to {ARTICLES_LIST_URL} ...")
    page.goto(ARTICLES_LIST_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    screenshot(page, "1-loaded")

    # Dump all buttons on the articles list page to find the pen/new icon
    print("Inspecting articles page for New Article button...")
    buttons = page.evaluate('''() => {
        return [...document.querySelectorAll("button, [role=button]")]
            .map(el => ({
                label: el.getAttribute("aria-label"),
                text: el.innerText.trim().substring(0, 40),
                testid: el.getAttribute("data-testid"),
                rect: JSON.stringify(el.getBoundingClientRect())
            }))
            .filter(el => el.label || el.text || el.testid);
    }''')
    print("  Buttons found:")
    for b in buttons:
        print(f"    {b}")

    # The pen/edit icon is in the top-right of the articles panel — target it by
    # aria-label or by position (rightmost button in the panel header area)
    print("Clicking New Article (pen) button...")
    try:
        new_btn = page.locator(
            '[aria-label="create"], [aria-label="New article"], '
            '[aria-label="Write article"], [aria-label="Create article"]'
        ).first
        new_btn.wait_for(state="visible", timeout=5000)
        new_btn.click()
        page.wait_for_timeout(2500)
        screenshot(page, "1b-composer")
    except Exception:
        # Fallback: click the rightmost button in the top area of the articles panel
        print("  Trying positional click on pen icon (top-right of articles panel)...")
        try:
            clicked = page.evaluate('''() => {
                const btns = [...document.querySelectorAll("button, [role=button]")];
                // Find buttons in the top ~60px of the viewport
                const topBtns = btns.filter(b => {
                    const r = b.getBoundingClientRect();
                    return r.top < 60 && r.right > 600;
                });
                if (topBtns.length) { topBtns[topBtns.length - 1].click(); return true; }
                return false;
            }''')
            if not clicked:
                raise RuntimeError("No top-right button found")
            page.wait_for_timeout(2500)
            screenshot(page, "1b-composer")
        except Exception as exc2:
            print(f"  Could not find New Article button: {exc2}")
            screenshot(page, "1-failed")
            print("Leaving browser open for manual intervention.")
            _wait_for_interrupt()
            return

    # --- Dump editable/input elements for selector diagnosis ---
    print("Inspecting composer editable fields...")
    fields = page.evaluate('''() => {
        const sel = '[contenteditable], input, textarea, h1, h2, [role="textbox"], [role="heading"]';
        return [...document.querySelectorAll(sel)].map((el, i) => ({
            index: i,
            tag: el.tagName,
            role: el.getAttribute("role"),
            contenteditable: el.getAttribute("contenteditable"),
            dataPlaceholder: el.getAttribute("data-placeholder"),
            ariaLabel: el.getAttribute("aria-label"),
            text: el.innerText ? el.innerText.trim().substring(0, 60) : (el.value || ''),
            rect: JSON.stringify(el.getBoundingClientRect())
        }));
    }''')
    for f in fields:
        print(f"  [{f['index']}] tag={f['tag']} role={f['role']} "
              f"ce={f['contenteditable']!r} placeholder={f['dataPlaceholder']!r} "
              f"label={f['ariaLabel']!r} text={f['text']!r} rect={f['rect']}")

    # --- Cover image ---
    # A hidden <input type="file"> sits in the cover area at ~y=198.
    # After upload, Twitter shows an "Edit media" crop dialog — dismiss with Apply.
    if image_path:
        print(f"Setting cover image: {image_path}")
        try:
            cover_input = page.locator('input[type="file"]').first
            cover_input.set_input_files(image_path)
            page.wait_for_timeout(2500)
            screenshot(page, "2a-cover-uploaded")
            # Dismiss the crop dialog if it appears
            try:
                # Wait up to 15s for the "Edit media" crop dialog to appear
                apply_btn = page.get_by_role("button", name=re.compile(r"^Apply$", re.I)).first
                apply_btn.wait_for(state="visible", timeout=15000)
                apply_btn.click()
                page.wait_for_timeout(1500)
                screenshot(page, "2b-cover-applied")
                print("  Cover crop dialog dismissed.")
            except Exception as e:
                # Fallback: JS click on any visible Apply button
                page.evaluate('''() => {
                    const b = [...document.querySelectorAll("button,[role=button]")]
                        .find(b => b.innerText.trim() === "Apply");
                    if (b) b.click();
                }''')
                page.wait_for_timeout(1500)
                print(f"  Crop dialog: {e} -- tried JS fallback")
        except Exception as exc:
            print(f"  cover image: {exc} -- continuing")

    # --- Title field ---
    # The title is a <textarea> at ~y=335 in the composer (first visible textarea)
    print("Entering title...")
    try:
        # Two textareas exist; the one at y>100 is the visible title field
        title_field = page.locator('textarea').filter(
            has=page.locator(':scope')
        ).nth(0)
        title_field.wait_for(state="attached", timeout=5000)
        title_field.focus()
        page.wait_for_timeout(300)
        page.keyboard.press("Meta+a")
        copy_to_clipboard(title)
        paste(page)
        screenshot(page, "3-title")
    except Exception as exc:
        print(f"  title field not found: {exc}")
        print("  Leaving browser open for manual intervention.")
        _wait_for_interrupt()
        return

    # --- Body field ---
    # The body is a <div role="textbox" contenteditable="true"> at ~y=511
    print("Entering body text...")
    try:
        # Segment-based composition: rich-copy text runs, insert REAL tables
        # via the Edit-block popup (tables keep their DOM order).
        compose_body_segments(page, context, html_path, dry_run=dry_run)

        # Wide-table (recs-wrap) link + preview image appended at the end
        if table_url:
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            copy_to_clipboard(f"Full table: {table_url}")
            paste(page)

        if body_image_path:
            page.keyboard.press("Enter")
            paste_image_clipboard(page, body_image_path)

        # Wait for the editor to finish autosaving (Publish button becomes enabled)
        print("  Waiting for autosave to complete...")
        page.wait_for_function(
            '''() => {
                const btns = [...document.querySelectorAll("button[role='button']")];
                return btns.some(b => b.innerText.trim() === "Publish" && !b.disabled);
            }''',
            timeout=30000
        )
        page.wait_for_timeout(500)
        screenshot(page, "4-body")
    except Exception as exc:
        print(f"  body field not found: {exc}")
        print("  Leaving browser open for manual intervention.")
        _wait_for_interrupt()
        return

    if dry_run:
        print("Dry run: article composed but NOT published. Review in browser.")
        dump_body_blocks(page, "dry-run-blocks")
        screenshot(page, "5-dry-run")
        _wait_for_interrupt()
        return

    # --- Publish ---
    print("Publishing article...")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1000)
    screenshot(page, "5-pre-publish")
    try:
        # Click via JS to bypass any transparent overlay in #layers
        clicked = page.evaluate('''() => {
            const btns = [...document.querySelectorAll("button[role='button']")];
            const pub = btns.filter(b => b.innerText.trim() === "Publish" && !b.disabled);
            if (pub.length) { pub[pub.length - 1].click(); return true; }
            return false;
        }''')
        if not clicked:
            raise RuntimeError("No enabled Publish button found via JS click")
        page.wait_for_timeout(2000)
        screenshot(page, "5b-dialog")

        # Confirm publish inside the dialog using a native Playwright click.
        # JS .click() doesn't fire React's synthetic events on this button.
        try:
            dialog_pub = page.locator(
                'div[aria-modal="true"] button, [role="dialog"] button'
            ).filter(has_text=re.compile(r"^Publish$", re.I)).first
            dialog_pub.wait_for(state="visible", timeout=8000)
            dialog_pub.click(force=True)
            page.wait_for_timeout(3000)
            print("  Dialog confirmed.")
        except Exception as exc2:
            # Fallback: native click on last visible Publish button
            print(f"  [WARN] dialog locator: {exc2} -- trying last Publish button")
            try:
                page.get_by_role("button", name=re.compile(r"^Publish$", re.I)).last.click(force=True)
                page.wait_for_timeout(3000)
            except Exception as exc3:
                print(f"  [WARN] fallback click: {exc3}")

        screenshot(page, "6-published")
        print("Article published.")
        log_page_count(context, "after-publish")
    except Exception as exc:
        print(f"  Publish button not found: {exc}")
        print("  Leaving browser open for manual intervention.")
        _wait_for_interrupt()


def _wait_for_interrupt() -> None:
    print("Press Ctrl+C to close.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _article_edit_url(edit_arg: str) -> str:
    """Normalise --edit value to a Twitter Article edit URL."""
    if edit_arg.startswith('http'):
        # Already a full URL — extract the numeric ID and rebuild canonically
        m = re.search(r'/(\d{15,20})(?:/edit)?$', edit_arg.rstrip('/'))
        if m:
            return f"https://x.com/compose/articles/edit/{m.group(1)}"
        return edit_arg  # unrecognised format, pass through
    # Bare numeric ID
    return f"https://x.com/compose/articles/edit/{edit_arg}"


def edit_article(page, context, html_path: str, edit_url: str, title: str, body: str,
                 image_path: str = None, body_image_path: str = None,
                 table_url: str = None, dry_run: bool = False) -> None:
    """Open an existing Twitter Article for editing and update its content."""
    print(f"Opening article for editing: {edit_url}")
    page.goto(edit_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    # Published articles show a read-only view with "Unpublish, move to drafts".
    # Click it so the full editor (with textarea + textbox) becomes available.
    try:
        unpub_btn = page.get_by_text("Unpublish, move to drafts", exact=True).first
        if unpub_btn.is_visible(timeout=3000):
            print("  Article is published -- unpublishing to enable editing...")
            unpub_btn.click()
            page.wait_for_timeout(2000)
            # Confirm in dialog if one appears
            try:
                confirm = page.get_by_role("button", name=re.compile(r"unpublish", re.I)).first
                confirm.wait_for(state="visible", timeout=5000)
                confirm.click()
                page.wait_for_timeout(2000)
            except Exception:
                pass
    except Exception:
        pass

    # Wait for the article editor to load -- it shows a <textarea> for the title.
    try:
        page.wait_for_selector('textarea', timeout=10000)
    except Exception:
        print("  Editor not loaded -- reloading...")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector('textarea', timeout=15000)
    page.wait_for_timeout(1000)
    screenshot(page, "1-edit-loaded")

    # Cover image (optional replacement)
    if image_path:
        print(f"Setting cover image: {image_path}")
        try:
            cover_input = page.locator('input[type="file"]').first
            cover_input.set_input_files(image_path)
            page.wait_for_timeout(2500)
            try:
                apply_btn = page.get_by_role("button", name=re.compile(r"^Apply$", re.I)).first
                apply_btn.wait_for(state="visible", timeout=15000)
                apply_btn.click()
                page.wait_for_timeout(1500)
            except Exception as e:
                page.evaluate('''() => {
                    const b = [...document.querySelectorAll("button,[role=button]")]
                        .find(b => b.innerText.trim() === "Apply");
                    if (b) b.click();
                }''')
                page.wait_for_timeout(1500)
                print(f"  Crop dialog JS fallback: {e}")
            screenshot(page, "2-cover-updated")
        except Exception as exc:
            print(f"  cover image: {exc} -- continuing")

    # Title
    print("Updating title...")
    try:
        title_field = page.locator('textarea').nth(0)
        title_field.wait_for(state="attached", timeout=5000)
        title_field.focus()
        page.keyboard.press("Meta+a")
        copy_to_clipboard(title)
        paste(page)
        screenshot(page, "3-title")
    except Exception as exc:
        print(f"  title field not found: {exc}")
        _wait_for_interrupt()
        return

    # Body
    print("Updating body text...")
    try:
        body_field = page.locator('div[role="textbox"][contenteditable="true"]').first
        body_field.wait_for(state="visible", timeout=10000)

        # Clear existing body content
        body_field.click()
        page.keyboard.press("Meta+a")
        page.wait_for_timeout(200)
        page.keyboard.press("Backspace")
        page.wait_for_timeout(300)

        # Segment-based composition: rich-copy text runs, insert REAL tables
        # via the Edit-block popup (tables keep their DOM order).
        compose_body_segments(page, context, html_path, dry_run=dry_run)

        # Wide-table (recs-wrap) link + preview image appended at the end
        if table_url:
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            copy_to_clipboard(f"Full table: {table_url}")
            paste(page)

        if body_image_path:
            page.keyboard.press("Enter")
            paste_image_clipboard(page, body_image_path)

        # Wait for autosave to complete before publishing
        print("  Waiting for autosave to complete...")
        page.wait_for_function(
            '''() => {
                const btns = [...document.querySelectorAll("button[role='button']")];
                return btns.some(b => b.innerText.trim() === "Publish" && !b.disabled);
            }''',
            timeout=30000
        )
        page.wait_for_timeout(500)
        screenshot(page, "4-body")
    except Exception as exc:
        print(f"  body field not found: {exc}")
        _wait_for_interrupt()
        return

    if dry_run:
        print("Dry run: article updated but NOT published. Review in browser.")
        dump_body_blocks(page, "dry-run-blocks")
        screenshot(page, "5-dry-run")
        _wait_for_interrupt()
        return

    print("Publishing updated article...")
    # Scroll to top so the Publish button in the header is in view and not obscured
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1000)
    screenshot(page, "5-pre-publish")
    try:
        # Click via JS to bypass any transparent overlay in #layers
        clicked = page.evaluate('''() => {
            const btns = [...document.querySelectorAll("button[role='button']")];
            const pub = btns.filter(b => b.innerText.trim() === "Publish" && !b.disabled);
            if (pub.length) { pub[pub.length - 1].click(); return true; }
            return false;
        }''')
        if not clicked:
            raise RuntimeError("No enabled Publish button found via JS click")
        page.wait_for_timeout(2000)
        screenshot(page, "5b-dialog")

        # Confirm publish inside the dialog using a native Playwright click.
        try:
            dialog_pub = page.locator(
                'div[aria-modal="true"] button, [role="dialog"] button'
            ).filter(has_text=re.compile(r"^Publish$", re.I)).first
            dialog_pub.wait_for(state="visible", timeout=8000)
            dialog_pub.click(force=True)
            page.wait_for_timeout(3000)
            print("  Dialog confirmed.")
        except Exception as exc2:
            print(f"  [WARN] dialog locator: {exc2} -- trying fallback")
            try:
                page.get_by_role("button", name=re.compile(r"^Publish$", re.I)).last.click(force=True)
                page.wait_for_timeout(3000)
            except Exception as exc3:
                print(f"  [WARN] fallback click: {exc3}")

        screenshot(page, "6-published")
        print("Article updated and published.")
    except Exception as exc:
        print(f"  Publish button not found: {exc}")
        _wait_for_interrupt()


# ---------------------------------------------------------------------------
# Delete a draft
# ---------------------------------------------------------------------------

def delete_draft_flow(page, edit_url: str) -> None:
    """Delete a draft via More > Delete Article > Yes, delete.

    Verified flow (2026-08-08): the confirm alertdialog ELEMENT reports a
    ZERO-SIZE rect and never registers visible -- wait on the "Yes, delete"
    BUTTON, not the dialog. Menu items respond to JS clicks; the confirm
    button needs a native click.
    """
    print(f"Opening draft for deletion: {edit_url}")
    page.goto(edit_url, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)

    # 1. More menu: topmost VISIBLE [aria-label=More] button
    mores = page.locator('[role="button"][aria-label="More"]:visible')
    best, best_y = None, 10**9
    for i in range(mores.count()):
        try:
            box = mores.nth(i).bounding_box()
            if box and box["y"] < best_y:
                best_y, best = box["y"], mores.nth(i)
        except Exception:
            pass
    if best is None:
        raise RuntimeError("No visible More button found for delete flow")
    best.click(force=True, timeout=5000)
    page.wait_for_timeout(1500)

    # 2. Delete Article menu item (JS click works on menu items)
    ok = page.evaluate("""() => {
        const els = [...document.querySelectorAll('[role="menuitem"], [role="button"]')];
        const d = els.find(el => (el.innerText || '').trim() === 'Delete Article'
                                 && el.offsetParent !== null);
        if (!d) return false;
        d.click();
        return true;
    }""")
    if not ok:
        raise RuntimeError("Delete Article menu item not found")
    page.wait_for_timeout(2000)

    # 3. Confirm: the affirmative button. The label has been BOTH "Yes,
    #    delete" (draft flow, 2026-08-08) and "Delete Article" inside the
    #    "Are you sure?" dialog (published articles, 2026-08-17) -- accept
    #    either; the dialog element itself is zero-size, wait on the button.
    confirm = None
    for label in ("Yes, delete", "Delete Article"):
        cand = page.get_by_role("button", name=label)
        if cand.count() > 0 and cand.last.is_visible():
            confirm = cand.last
            break
    if confirm is None:
        raise RuntimeError("No delete confirmation button found")
    confirm.click(force=True, timeout=8000)
    page.wait_for_timeout(4000)
    print("  Article deleted.")


# ---------------------------------------------------------------------------
# Dedup — find existing article by title
# ---------------------------------------------------------------------------

def find_existing_article(context, title: str) -> str | None:
    """Check Published tab for an article matching 'title'. Return edit URL or None."""
    page = context.new_page()
    try:
        page.goto(ARTICLES_LIST_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        # Click Published tab
        tab = page.locator("text=Published").first
        if tab.count() > 0:
            tab.click()
            page.wait_for_timeout(4000)
        # Search for the title in the published list. Iterate selectors and
        # only stop when a match is found -- the old code broke out of the
        # selector loop after the FIRST selector (div[role='link'] is always
        # empty on this page; the articles live in <a> elements), so dedup
        # never matched and every run published a duplicate.
        slug_key = title.lower().replace("'", "").replace('"', "").replace(" ", "-")[:40]
        for sel in ["div[role='link']", "a"]:
            els = page.locator(sel)
            for i in range(min(els.count(), 30)):
                try:
                    txt = els.nth(i).inner_text(timeout=800)
                except Exception:
                    continue
                if title[:40].lower() in txt.lower() or slug_key in txt.lower():
                    href = els.nth(i).evaluate("el => el.href") or ""
                    if "/edit/" in href:
                        page.close()
                        return href
        page.close()
        return None
    except Exception:
        try:
            page.close()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish or edit an archerships.com essay as a Twitter/X Article."
    )
    parser.add_argument("file", nargs="?", help="Path to essay HTML file")
    parser.add_argument("--essay", metavar="PATH",
                        help="Essay HTML or MD file; reads/writes twitter id from frontmatter")
    parser.add_argument("--edit", metavar="URL_OR_ID",
                        help="Edit existing article by URL (https://x.com/i/article/ID) or bare ID")
    parser.add_argument("--image", "-i", help="Cover image path")
    parser.add_argument("--delete", metavar="URL_OR_ID",
                        help="Delete a draft article by URL or bare ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compose/edit the article but do not publish")
    args = parser.parse_args()

    # --delete mode: no essay file needed
    if args.delete:
        CHROMIUM_APP = "/Applications/Chromium.app"
        CHROMIUM_BIN = os.path.join(CHROMIUM_APP, "Contents/MacOS/Chromium")

        def _ensure_chromium() -> None:
            """Launch Chromium with remote debugging if not already running at CDP_URL."""
            import urllib.request, urllib.error
            try:
                urllib.request.urlopen(CDP_URL + "/json/version", timeout=2)
                return  # already running
            except urllib.error.URLError:
                pass
            if not os.path.isfile(CHROMIUM_BIN):
                sys.exit(f"Chromium not found at {CHROMIUM_BIN}. Install via: brew install --cask chromium")
            print(f"Launching Chromium with remote debugging on port {CDP_PORT}...")
            subprocess.Popen(
                ["open", "-a", CHROMIUM_APP, "--args", f"--remote-debugging-port={CDP_PORT}",
                 "--no-first-run", "--no-startup-window", "--new-window",
                 "--load-extension=/Users/crasch/av/ext/imp-translate"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for i in range(30):
                time.sleep(1)
                try:
                    urllib.request.urlopen(CDP_URL + "/json/version", timeout=2)
                    print("  Chromium is ready.")
                    return
                except urllib.error.URLError:
                    continue
            sys.exit(f"Timed out waiting for Chromium CDP on port {CDP_PORT}.")

        _ensure_chromium()
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(CDP_URL)
            except Exception as exc:
                sys.exit(f"CDP connection failed: {exc}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                delete_draft_flow(page, _article_edit_url(args.delete))
            except Exception as exc:
                print(f"Error: {exc}")
            finally:
                browser.close()
        return

    # --essay mode: resolve HTML path and article ID from frontmatter
    essay_md = None
    if args.essay:
        essay_path = Path(os.path.expanduser(args.essay)).resolve()
        if essay_path.suffix == '.md':
            essay_md = essay_path
            html_path = str(essay_path.with_suffix('.html'))
        else:
            html_path = str(essay_path)
            essay_md = find_md_for_html(essay_path)
        args.file = html_path

        if essay_md and not args.edit:
            fm = load_fm(essay_md)
            entry = get_platform_entry(fm, 'twitter')
            if entry and entry.get('id'):
                args.edit = str(entry['id'])
                print(f"  [frontmatter] Twitter article ID: {args.edit}")
            else:
                print("  [frontmatter] No Twitter ID found -- will publish new article")

    if not args.file:
        parser.error("Provide an essay HTML file or use --essay")

    html_path = os.path.expanduser(args.file)
    if not os.path.exists(html_path):
        sys.exit(f"File not found: {html_path}")

    print(f"Extracting essay from: {html_path}")
    title, body = extract_essay(html_path)
    print(f"  Title : {title}")
    print(f"  Body  : {len(body)} characters, {body.count(chr(10))+1} lines")

    image_path = os.path.expanduser(args.image) if args.image else detect_cover_image(html_path)
    if image_path:
        print(f"  Cover : {image_path}")
    else:
        print(f"  Cover : (none)")

    _raw_preview = detect_table_preview_image(html_path)
    _temp_preview = None
    if _raw_preview:
        print(f"  Preview : {_raw_preview}")
        body_image_path = prepare_preview_image(_raw_preview)
        if body_image_path != _raw_preview:
            _temp_preview = body_image_path
    else:
        body_image_path = None
        print(f"  Preview : (none)")

    table_url = detect_table_url(html_path)
    if table_url:
        print(f"  Table : {table_url}")

    CHROMIUM_APP = "/Applications/Chromium.app"
    CHROMIUM_BIN = os.path.join(CHROMIUM_APP, "Contents/MacOS/Chromium")

    def _ensure_chromium() -> None:
        """Launch Chromium with remote debugging if not already running at CDP_URL."""
        import urllib.request, urllib.error
        try:
            urllib.request.urlopen(CDP_URL + "/json/version", timeout=2)
            return  # already running
        except urllib.error.URLError:
            pass
        if not os.path.isfile(CHROMIUM_BIN):
            sys.exit(f"Chromium not found at {CHROMIUM_BIN}. Install via: brew install --cask chromium")
        print(f"Launching Chromium with remote debugging on port {CDP_PORT}...")
        subprocess.Popen(
            ["open", "-a", CHROMIUM_APP, "--args", f"--remote-debugging-port={CDP_PORT}",
             "--no-first-run", "--no-startup-window", "--new-window",
             "--load-extension=/Users/crasch/av/ext/imp-translate"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait for CDP to become available
        for i in range(30):
            time.sleep(1)
            try:
                urllib.request.urlopen(CDP_URL + "/json/version", timeout=2)
                print("  Chromium is ready.")
                return
            except urllib.error.URLError:
                continue
        sys.exit(f"Timed out waiting for Chromium CDP on port {CDP_PORT}.")

    _ensure_chromium()

    with sync_playwright() as p:
        print(f"Connecting to Chromium via CDP ({CDP_URL})...")
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            sys.exit(f"CDP connection failed: {exc}")

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        try:
            if args.edit:
                edit_url = _article_edit_url(args.edit)
                edit_article(page, context, html_path, edit_url, title, body,
                             image_path=image_path, body_image_path=body_image_path,
                             table_url=table_url, dry_run=args.dry_run)
            else:
                # Dedup: check if an article with this title already exists
                existing = None if args.dry_run else find_existing_article(context, title)
                if existing:
                    print(f"Dedup: found existing article at {existing} — editing in place.")
                    edit_article(page, context, html_path, existing, title, body,
                                 image_path=image_path, body_image_path=body_image_path,
                                 table_url=table_url, dry_run=args.dry_run)
                else:
                    publish_article(page, context, html_path, title, body,
                                    image_path=image_path, body_image_path=body_image_path,
                                    table_url=table_url, dry_run=args.dry_run)
                # After a new publish, wait for X to redirect to the article's
                # status page, then write the article ID back to frontmatter.
                if essay_md and not args.dry_run:
                    aid = None
                    for _ in range(20):
                        # URL shapes: /status/ID (status page), /i/article/ID,
                        # /archerships/article/ID (public article), and
                        # /compose/articles/edit/ID (composer stays on the
                        # edit URL after publishing an update).
                        m = re.search(
                            r'/(?:status|i/article|archerships/article|compose/articles/edit)/(\d{15,20})',
                            page.url)
                        if m:
                            aid = m.group(1)
                            break
                        page.wait_for_timeout(1500)
                    if aid:
                        set_platform_field(essay_md, 'twitter', id=aid,
                                           url=f"https://x.com/archerships/article/{aid}")
                        print(f"  [frontmatter] Wrote Twitter id={aid} to {essay_md.name}")
                    else:
                        print(f"  Could not extract article ID from URL ({page.url}).")
                        print(f"  Add it manually: set_platform_field(md, 'twitter', id='ID')")
        except Exception as exc:
            print(f"Error: {exc}")
            _wait_for_interrupt()
        finally:
            browser.close()
            if _temp_preview and os.path.exists(_temp_preview):
                os.unlink(_temp_preview)


if __name__ == "__main__":
    main()
