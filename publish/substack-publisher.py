#!/Users/crasch/av/venv/hydra/bin/python3
"""
substack-publisher.py
Playwright-based Substack automation: create new drafts or edit existing posts.

Usage:
  # Create new drafts from all HTML files in ASSEMBLY_DIR:
  substack-publisher.py

  # Edit an existing post by post ID, using HTML from ASSEMBLY_DIR:
  substack-publisher.py --edit POST_ID --file post.html

  # Edit using an absolute file path:
  substack-publisher.py --edit POST_ID --file /path/to/post.html

  # Edit via essay frontmatter (reads Substack id from published_at):
  substack-publisher.py --essay ~/av/doc/posts/SLUG/SLUG.md

  # Override publication slug (default: archerships):
  substack-publisher.py --pub mypub --edit POST_ID --file post.html
"""

import argparse
import os
import re
import sys
import time
import keyring
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from essay_frontmatter import load_fm, get_platform_entry, set_platform_field, find_md_for_html
sys.path.insert(0, os.path.expanduser('~/av/bin/archerships'))
from contact_snippet import load_contact_snippet as shared_load_contact_snippet

ASSEMBLY_DIR  = os.environ.get('HYDRA_ASSEMBLY_DIR',
                               os.path.expanduser("~/av/doc/ai/substack/assembly"))
SUBSTACK_URL  = "https://substack.com/sign-in"
DEFAULT_PUBLICATION = "archerships"

ARCHERSHIPS   = Path(os.environ.get('ARCHERSHIPS_ROOT',
                     str(Path.home() / 'av' / 'prj' / 'archerships.com')))


def load_contact_snippet() -> str:
    """Contact snippet from the canonical fb-contact.txt (shared module)."""
    return shared_load_contact_snippet()


def get_credentials():
    email = keyring.get_password('substack', 'email')
    password = keyring.get_password('substack', 'password')
    if not email or not password:
        print("Error: Substack credentials not found in system keyring.")
        print("Set them with:")
        print("  python3 -c \"import keyring; keyring.set_password('substack', 'email', 'you@example.com')\"")
        print("  python3 -c \"import keyring; keyring.set_password('substack', 'password', 'yourpassword')\"")
        sys.exit(1)
    return email, password


TABLE_BASE_URL = 'https://archerships.com/tables'


def _replace_tables_with_previews(body_html: str) -> str:
    """Replace <div class="recs-wrap" data-table-slug="..."> blocks with preview image + link."""
    soup = BeautifulSoup(body_html, 'html.parser')
    for div in soup.find_all('div', class_='recs-wrap'):
        table_url = div.get('data-table-url', '').strip()
        title     = div.get('data-table-title', '').strip()
        if not table_url:
            continue
        slug        = div.get('data-table-slug', '').strip()
        preview_url = f'{TABLE_BASE_URL}/{slug}-table-preview-1280x720.png'
        replacement = BeautifulSoup(
            f'<figure>'
            f'<a href="{table_url}">'
            f'<img src="{preview_url}" alt="{title}" style="max-width:100%;">'
            f'</a>'
            f'<figcaption>'
            f'<a href="{table_url}">Click here to see {title}</a>'
            f'</figcaption>'
            f'</figure>',
            'html.parser'
        )
        div.replace_with(replacement)
    return str(soup)


def load_html(file_arg, contact_snippet: str = ''):
    """
    Resolve file_arg to an absolute path and return (title, subtitle, body_html).

    Title:    extracted from <h1>; falls back to filename stem.
    Subtitle: extracted from <p class="subtitle">; falls back to first sentence
              of the first non-empty <p> inside <main>.
    Body:     inner HTML of <main> (or full body if no <main>), with the
              also-published block stripped and the contact snippet appended.
    """
    path = file_arg if os.path.isabs(file_arg) else os.path.join(ASSEMBLY_DIR, file_arg)
    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        sys.exit(1)

    raw = open(path, encoding='utf-8').read()
    soup = BeautifulSoup(raw, 'html.parser')

    # --- Title ---
    h1 = soup.find('h1')
    title = h1.get_text(strip=True) if h1 else os.path.basename(path).replace('.html', '')

    # --- Subtitle ---
    subtitle = ''
    sub_el = soup.find('p', class_='subtitle')
    if sub_el:
        subtitle = sub_el.get_text(strip=True)
    else:
        main_el = soup.find('main') or soup.find('body') or soup
        for p in main_el.find_all('p'):
            text = p.get_text(' ', strip=True)
            if text:
                m = re.search(r'[^.!?]*[.!?]', text)
                subtitle = m.group(0).strip() if m else text[:200]
                if len(subtitle) > 200:
                    subtitle = subtitle[:197] + '...'
                break

    # --- Body: extract <main> inner content ---
    main_el = soup.find('main') or soup.find('body') or soup
    # Remove also-published and site-specific nav elements
    for el in main_el.find_all(id=['site-breadcrumb', 'site-nav', 'site-footer',
                                   'also-published', 'contact-snippet']):
        el.decompose()
    for el in main_el.find_all(class_='also-published'):
        el.decompose()
    body_html = main_el.decode_contents().strip()

    # Replace embedded tables with preview image + link
    body_html = _replace_tables_with_previews(body_html)

    # Rewrite relative img src paths (e.g. "img/file.webp") to absolute Arweave URLs
    body_html = re.sub(
        r'(<img\b[^>]*\bsrc=")(?!https?://)([^"]+)(")',
        r'\1https://archerships.arweave.net/essays/\2\3',
        body_html
    )

    if contact_snippet:
        body_html += '\n' + contact_snippet

    return title, subtitle, body_html


def set_subtitle(page, subtitle: str) -> bool:
    """Try to fill the Substack subtitle/preview field. Returns True if found."""
    sels = [
        'textarea[placeholder*="preview" i]',
        'input[placeholder*="preview" i]',
        'textarea[placeholder*="subtitle" i]',
        'input[placeholder*="subtitle" i]',
        '[data-testid="post-subtitle"]',
    ]
    for sel in sels:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.fill(subtitle)
                return True
        except Exception:
            pass
    return False


def set_editor_content(page, title, subtitle, content):
    """Write title, subtitle, and body into the Substack editor."""
    # --- Title ---
    if page.query_selector('textarea#post-title'):
        title_sel = 'textarea#post-title'
    else:
        title_sel = 'textarea[placeholder="Type your title..."]'

    page.wait_for_selector(title_sel, timeout=30000)
    page.fill(title_sel, title)

    # --- Subtitle ---
    if subtitle:
        ok = set_subtitle(page, subtitle)
        if not ok:
            print(f"  [WARN] subtitle field not found; skipping subtitle: {subtitle[:60]}")

    # --- Body ---
    if page.query_selector('.tiptap.ProseMirror.mousetrap'):
        editor_sel = '.tiptap.ProseMirror.mousetrap'
    elif page.query_selector('.editor-v2 .ProseMirror'):
        editor_sel = '.editor-v2 .ProseMirror'
    else:
        editor_sel = '.ProseMirror'

    page.click(editor_sel)
    page.keyboard.press('Meta+A')
    page.keyboard.press('Backspace')
    # Use execCommand('insertHTML') so TipTap/ProseMirror processes the new
    # content through its paste handler (updates internal state + triggers
    # image upload), rather than bypassing it via innerHTML assignment.
    page.evaluate(
        'document.execCommand("selectAll", false, null);'
        'document.execCommand("insertHTML", false, ' + repr(content) + ');'
    )


def click_publish(page, section: str = 'Acceleration Nation'):
    """
    Click Continue, configure publish settings, then publish.

    Defaults (per pol/SUBSTACK.md):
      Audience:  Everyone
      Comments:  Everyone
      Section:   Acceleration Nation
      Tags:      none
      Delivery:  email unchecked (web-only publish)
    """
    # Step 1: Continue
    print("  Clicking Continue...")
    page.wait_for_selector('button:has-text("Continue")', timeout=15000).click()
    page.wait_for_selector('[role="dialog"]', timeout=10000)
    time.sleep(1)

    # Step 2: Audience -> Everyone (first radio labeled "Everyone")
    print("  Setting audience: Everyone...")
    page.locator('[role="dialog"] label:has-text("Everyone")').first.click()
    time.sleep(0.5)

    # Step 3: Comments -> Everyone (second radio labeled "Everyone")
    print("  Setting comments: Everyone...")
    everyone_labels = page.locator('[role="dialog"] label:has-text("Everyone")')
    if everyone_labels.count() >= 2:
        everyone_labels.nth(1).click()
        time.sleep(0.5)

    # Step 4: Section
    if section:
        print(f"  Setting section: {section}...")
        try:
            # Try native <select> first
            select_el = page.locator('[role="dialog"] select').first
            if select_el.count():
                select_el.select_option(label=section)
                time.sleep(0.5)
            else:
                # Fall back to custom dropdown trigger
                section_trigger = page.locator('[role="dialog"]').get_by_text('Choose a section')
                if section_trigger.count():
                    section_trigger.click()
                    time.sleep(0.5)
                    page.get_by_role('option', name=section).click()
                    time.sleep(0.5)
        except Exception as e:
            print(f"  [WARN] Could not set section: {e}")

    # Step 5: Uncheck email delivery
    print("  Unchecking email delivery...")
    try:
        delivery_cb = page.locator('label:has-text("Send via email and the Substack app")')
        if delivery_cb.count():
            cb_input = delivery_cb.locator('input[type="checkbox"]')
            if cb_input.count() and cb_input.is_checked():
                delivery_cb.click()
                time.sleep(0.3)
    except Exception as e:
        print(f"  [WARN] Could not uncheck email delivery: {e}")

    # Step 6: Publish
    print("  Publishing...")
    publish_btn = page.wait_for_selector(
        'button:has-text("Send to everyone now"), button:has-text("Publish now"), button:has-text("Send to")',
        timeout=10000
    )
    publish_btn.click()

    # Step 7: Handle "Do you want to send via email?" confirmation dialog
    try:
        web_only_btn = page.wait_for_selector(
            'button:has-text("Publish on web only")',
            timeout=5000
        )
        print("  Selecting web-only delivery...")
        web_only_btn.click()
    except Exception:
        pass  # No confirmation dialog -- already handled or not shown

    # Step 8: Confirm published
    try:
        page.wait_for_selector(
            'text=/your post is live/i',
            timeout=20000
        )
        print("  Published successfully.")
    except Exception:
        time.sleep(3)
        if '/new' not in page.url and 'share-center' in page.url:
            print(f"  Published. Share center URL: {page.url}")
        elif '/new' not in page.url:
            print(f"  Published. Post URL: {page.url}")
        else:
            print("  Could not confirm publish state -- check browser.")


def _extract_post_id_from_url(url: str) -> str | None:
    """Extract numeric post ID from a Substack publish URL."""
    m = re.search(r'/publish/post/(\d+)', url)
    return m.group(1) if m else None


def upload_cover_image(page, image_path: str) -> bool:
    """Upload a cover image to the Substack post via the Settings > Thumbnail upload.
    
    The Substack editor has a hidden file input (accept=image/*) in the settings
    sidebar. Click the Settings button to reveal it, then upload via the file input.
    Returns True on success.
    """
    if not image_path or not os.path.isfile(image_path):
        print("  [COVER] No image to upload, or file not found.")
        return False

    print(f"  [COVER] Uploading: {Path(image_path).name}")

    # Click Settings button to reveal the sidebar with the Thumbnail section
    try:
        page.wait_for_timeout(2000)  # let editor fully render
        settings_btn = page.locator('button:has-text("Settings")').first
        settings_btn.click(force=True)
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"  [COVER] Settings button not found: {e}")
        return False

    # Use Playwright's file chooser to upload the cover image.
    # The Upload label is in the Settings sidebar which has its own scroll
    # container — Playwright can't click elements outside the viewport even
    # with force=True. Use a JS click to trigger the native file dialog.
    try:
        with page.expect_file_chooser() as fc_info:
            page.evaluate('() => {'
                          '  const el = document.querySelector(\'label[for="file-sidebar-file-input"]\');'
                          '  if (el) el.click();'
                          '}')
        file_chooser = fc_info.value
        file_chooser.set_files(image_path)
        print("  [COVER] File sent via chooser (JS click).")
    except Exception as e:
        print(f"  [COVER] File chooser failed: {e}")
        # Fallback: direct set_input_files
        try:
            page.locator('input[type="file"][accept*="image"]').first.set_input_files(image_path)
            print("  [COVER] File sent via set_input_files fallback.")
        except Exception as e2:
            print(f"  [COVER] Upload failed (both methods): {e2}")
            return False

    page.wait_for_timeout(4000)  # wait for upload + crop dialog

    # The crop dialog may appear ("Will be cropped to a 3:2 aspect ratio")
    # Click Done/Save if a dialog appears
    try:
        done_btn = page.wait_for_selector(
            'button:has-text("Done"), button:has-text("Save"), button:has-text("Apply")',
            timeout=5000
        )
        done_btn.click()
        print("  [COVER] Crop dialog dismissed.")
        page.wait_for_timeout(1500)
    except Exception:
        pass  # No crop dialog — upload completed without confirmation

    # Verify the upload — check if the thumbnail area shows the image
    try:
        has_image = page.evaluate('''() => {
            const imgs = document.querySelectorAll('img[src*="substackcdn.com"], img[alt="cover"], img[src*="substack-post"]');
            return imgs.length > 0;
        }''')
        if has_image:
            print("  [COVER] Cover image uploaded successfully.")
        else:
            print("  [COVER] Upload may have succeeded — no visible confirmation.")
    except Exception:
        pass

    return True


def publish_draft(page, html_file, pub, contact_snippet: str = '',
                  image_path: str = '') -> str | None:
    """Publish a new post. Returns the numeric post ID if extractable, else None."""
    title, subtitle, content = load_html(html_file, contact_snippet)
    print(f"Publishing: {title}")
    if subtitle:
        print(f"  Subtitle: {subtitle[:80]}")
    page.goto(f"https://{pub}.substack.com/publish/post/new")
    # Upload cover image BEFORE content injection (crop dialog varies timing)
    if image_path:
        upload_cover_image(page, image_path)
    set_editor_content(page, title, subtitle, content)
    print(f"  Content injected.")
    time.sleep(5)  # give Substack autosave time to register the content
    click_publish(page)
    return _extract_post_id_from_url(page.url)


def edit_post(page, post_id, html_file, pub, contact_snippet: str = '') -> None:
    title, subtitle, content = load_html(html_file, contact_snippet)
    print(f"Editing post {post_id}: {title}")
    page.goto(f"https://{pub}.substack.com/publish/post/{post_id}")
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_selector('textarea#post-title', timeout=30000)
    set_editor_content(page, title, subtitle, content)

    time.sleep(3)
    update_btn = page.query_selector('button:has-text("Update")')
    if not update_btn:
        print("Update button not found -- content injected but not published. Save manually.")
        return

    print("Clicking Update...")
    update_btn.click()

    # Substack opens a settings dialog; click "Update now" to confirm.
    try:
        update_now = page.wait_for_selector(
            'button:has-text("Update now")', timeout=10000
        )
        print("Clicking Update now...")
        update_now.click()
    except Exception:
        print("  [WARN] 'Update now' button not found -- checking for direct save...")

    # Wait for confirmation
    try:
        page.wait_for_selector('button:has-text("Saved")', timeout=20000)
        print(f"Post {post_id} updated successfully.")
    except Exception:
        time.sleep(3)
        url = page.url
        if f'/post/{post_id}' in url or 'share-center' in url:
            print(f"Post {post_id} updated (confirmed via URL: {url})")
        else:
            print(f"Could not confirm update -- check browser. Current URL: {url}")


def login(page, email, password):
    print("Logging in to Substack...")
    page.goto(SUBSTACK_URL)
    page.fill('input[name="email"]', email)
    page.click('button:has-text("Confirm")')
    print("If a magic link was sent, click it in the browser. Waiting for dashboard...")
    try:
        page.wait_for_url("**/dashboard**", timeout=90000)
        print("Login successful.")
    except Exception:
        print("Login timed out. Complete login manually in the browser, then press Enter here.")
        input()


def main():
    parser = argparse.ArgumentParser(description="Substack draft creator / post editor")
    parser.add_argument("--essay", metavar="PATH",
                        help="Essay HTML or MD file; reads/writes Substack id from frontmatter")
    parser.add_argument("--edit", metavar="POST_ID",
                        help="Edit an existing post by its numeric post ID")
    parser.add_argument("--file", metavar="FILE",
                        help="HTML file to use (basename resolved against ASSEMBLY_DIR, or absolute path)")
    parser.add_argument("--new-post", metavar="FILE",
                        help="Create a single new draft from an absolute HTML file path")
    parser.add_argument("--image", metavar="FILE",
                        help="Cover image path (adapted to substack-cover 1200x600). "
                             "Uploaded via Settings > Thumbnail before content injection.")
    parser.add_argument("--pub", metavar="SLUG", default=DEFAULT_PUBLICATION,
                        help=f"Publication subdomain slug (default: {DEFAULT_PUBLICATION})")
    args = parser.parse_args()

    # --essay mode: resolve HTML + post ID from frontmatter
    essay_md = None
    if args.essay:
        essay_path = Path(os.path.expanduser(args.essay)).resolve()
        if essay_path.suffix == '.md':
            essay_md = essay_path
            html_path = essay_path.with_suffix('.html')
        else:
            html_path = essay_path
            essay_md = find_md_for_html(essay_path)

        if not html_path.exists():
            sys.exit(f"Essay HTML not found: {html_path}")

        args.file = str(html_path)

        if essay_md and not args.edit:
            fm = load_fm(essay_md)
            entry = get_platform_entry(fm, 'substack')
            if entry and entry.get('id'):
                args.edit = str(entry['id'])
                print(f"  [frontmatter] Substack post ID: {args.edit}")
            else:
                print("  [frontmatter] No Substack ID found -- will create new post")

    if args.edit and not args.file:
        parser.error("--edit requires --file")

    contact_snippet = load_contact_snippet()

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
        except Exception as e:
            print(f"Could not connect to Brave on port 9222: {e}")
            print("Launch Brave with: open -a \"Brave Browser\" --args --remote-debugging-port=9222")
            sys.exit(1)

        if args.edit:
            try:
                edit_post(page, args.edit, args.file, args.pub, contact_snippet)
            except Exception as e:
                print(f"Error editing post {args.edit}: {e}")
        elif args.new_post or (args.essay and args.file):
            html = args.new_post or args.file
            image_path = os.path.abspath(os.path.expanduser(args.image)) if args.image else ''
            try:
                post_id = publish_draft(page, os.path.abspath(os.path.expanduser(html)),
                                        args.pub, contact_snippet, image_path=image_path)
                if post_id and essay_md:
                    set_platform_field(essay_md, 'substack', id=post_id)
                    print(f"  [frontmatter] Wrote Substack id={post_id} to {essay_md.name}")
            except Exception as e:
                print(f"Error publishing {html}: {e}")
        else:
            html_files = [f for f in os.listdir(ASSEMBLY_DIR) if f.endswith(".html")]
            if not html_files:
                print(f"No HTML files found in {ASSEMBLY_DIR}")
                browser.close()
                return
            for html_file in html_files:
                try:
                    publish_draft(page, html_file, args.pub, contact_snippet)
                except Exception as e:
                    print(f"Error publishing {html_file}: {e}")

        print("\nDone.")


if __name__ == "__main__":
    main()
