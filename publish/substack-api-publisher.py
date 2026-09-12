#!/Users/crasch/av/venv/hydra/bin/python3
"""
substack-api-publisher.py -- Publish an archerships.com essay to Substack
via the unofficial API, without Playwright or a running browser.

Authentication: loads ~/.config/substack/cookies.json; if missing or expired,
extracts session cookies from Brave's SQLite store automatically (macOS).
API login (email/password) is not used -- Substack now requires CAPTCHA.

Usage:
  substack-api-publisher.py --essay ~/av/doc/posts/SLUG/SLUG.md [--dry-run]
  substack-api-publisher.py --essay ~/av/doc/posts/SLUG/SLUG.md --cover path/to/img.webp

The script:
  1. Reads the essay .md for title, subtitle, section, and body
  2. Strips pandoc image attributes ({alt=... width=... height=...})
  3. Resolves relative image paths and uploads them to Substack CDN
  4. Creates a draft, sets section/audience, publishes (web-only, no email)
  5. Fetches the published URL and writes it back to published_at frontmatter
  6. Re-renders the essay HTML
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# Use the python-substack (ma2za) library
try:
    import substack as substack_lib
    from substack import Api
    from substack.post import Post
except ImportError:
    sys.exit("python-substack not found: pip install python-substack")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from essay_frontmatter import load_fm, set_platform_field
from hydra_paths import CONTENT_DIR, RENDERER

COOKIES_PATH  = Path.home() / '.config' / 'substack' / 'cookies.json'
PUBLICATION   = os.environ.get('SUBSTACK_PUBLICATION', 'archerships')
SECTION_NAME  = 'Acceleration Nation'
API_URL       = f'https://{PUBLICATION}.substack.com/api/v1'
PUB_URL       = f'https://{PUBLICATION}.substack.com'

# ── Markdown preprocessing ───────────────────────────────────────────────────

# Strip pandoc extended image attributes: ![alt](img.webp){alt="..." width="800" ...}
_PANDOC_ATTRS = re.compile(r'(\!\[[^\]]*\]\([^)]+\))\{[^}]*\}')

# Match image lines: ![alt](path) -- captures path
_IMG_LINE = re.compile(r'^\s*!\[[^\]]*\]\(([^)]+)\)\s*$')


def strip_pandoc_attrs(md: str) -> str:
    return _PANDOC_ATTRS.sub(r'\1', md)


def resolve_image_paths(md: str, base_dir: Path) -> str:
    """Replace relative image paths with absolute paths."""
    def replace(m):
        full = m.group(0)
        line_m = _IMG_LINE.match(full)
        if not line_m:
            return full
        img_path = line_m.group(1)
        if img_path.startswith('http://') or img_path.startswith('https://'):
            return full
        abs_path = (base_dir / img_path).resolve()
        if abs_path.exists():
            return full.replace(img_path, str(abs_path))
        return full
    return '\n'.join(replace(re.match(r'.*', line) or type('', (), {'group': lambda s, n: line})())
                    if _IMG_LINE.match(line) else line
                    for line in md.splitlines())


def preprocess_markdown(md: str, essay_dir: Path) -> str:
    md = strip_pandoc_attrs(md)
    # Strip the renderer's \@ escape (citekey protection, meaningless on
    # Substack -- without this, \@handle renders with a literal backslash).
    md = md.replace('\\@', '@')
    # Replace relative image refs with absolute paths
    def fix_img(m):
        path = m.group(1)
        if path.startswith(('http://', 'https://', '/')):
            return m.group(0)
        abs_p = (essay_dir / path).resolve()
        return m.group(0).replace(path, str(abs_p)) if abs_p.exists() else m.group(0)
    md = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', lambda m: m.group(0).replace(
        m.group(0), '![' + m.group(1) + '](' + (
            str((essay_dir / m.group(2)).resolve()) if not m.group(2).startswith(('http', '/'))
            and (essay_dir / m.group(2)).exists() else m.group(2)
        ) + ')'), md)
    return md

# ── Arweave inline image upload (--arweave) ─────────────────────────────────
#
# When --arweave is set, every local inline image in the markdown body is
# uploaded to Arweave (bin/archerships/arweave-upload.mjs) and the markdown
# link rewritten to its permanent https://arweave.net/{txId}. from_markdown
# then sees a URL (not a local path), skips Substack S3 upload, and stores the
# Arweave URL on the captionedImage/image2 node.
_ARWEAVE_UPLOADER = Path.home() / 'av' / 'bin' / 'archerships' / 'arweave-upload.mjs'

def upload_to_arweave(image_path: Path, paid: bool = False) -> str | None:
    """Upload one local image to Arweave; return the https://arweave.net/{tx}
    URL, or None on failure. Free tier by default; oversized files that can't
    be uploaded are reported (not charged) unless paid=True."""
    if not _ARWEAVE_UPLOADER.exists():
        print(f'[arweave] WARN: uploader not found at {_ARWEAVE_UPLOADER}')
        return None
    try:
        cmd = ['node', str(_ARWEAVE_UPLOADER), str(image_path)]
        if paid:
            cmd.append('--paid')
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        m = re.search(r'https://arweave\.net/[A-Za-z0-9_-]+', r.stdout)
        if not m:
            detail = (r.stdout or r.stderr or '').strip()
            print(f'[arweave] upload failed for {image_path.name}: {detail[:120]}')
            return None
        return m.group(0)
    except Exception as e:
        print(f'[arweave] upload error {image_path.name}: {e}')
        return None


def arweave_rewrite_images(md: str, essay_dir: Path, paid: bool = False) -> tuple:
    """Replace local markdown image paths with Arweave URLs.

    Returns (new_md, url_map) where url_map maps absolute image path ->
    arweave URL (used to attach per-image metadata in the draft body).
    """
    url_map = {}
    def replace(m):
        alt = m.group(1)
        path = m.group(2)
        if path.startswith(('http://', 'https://')):
            return m.group(0)                      # remote URL; leave as-is
        # local path (relative, or absolute like /Users/...); resolve it
        p = Path(path)
        abs_p = p if p.is_absolute() else (essay_dir / path)
        abs_p = abs_p.resolve()
        if not abs_p.exists():
            return m.group(0)
        key = str(abs_p)
        url = url_map.get(key)
        if url is None:
            url = upload_to_arweave(abs_p, paid=paid)
            if url:
                url_map[key] = url
            else:
                # keep local path; from_markdown will upload it via api
                return m.group(0)
        return f'![{alt}]({url})'
    new_md = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace, md)
    return new_md, url_map


# ── Image metadata (alt / caption / tags) ────────────────────────────────────

def _load_image_metadata_yaml(path) -> dict:
    """Load a YAML file mapping image path -> {alt, caption, tags}."""
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data


def parse_image_metadata(body_md: str) -> dict:
    """Parse alt/caption/tags for each inline image from the markdown body.

    Sources, in priority order:
      1. --image-metadata YAML file (explicit) -- applied later in main()
      2. Raw <figure> blocks: <img alt="..."> -> alt, <figcaption> -> caption,
         and a trailing <!-- tags: a, b, c --> comment inside the figure.
      3. Bare markdown image ![alt](url) -> alt = alt text.

    Returns {image_url_or_path: {'alt':..., 'caption':..., 'tags':[...]}}.
    """
    meta = {}
    # (a) raw <figure> blocks
    for fig in re.findall(r'(<figure>.*?</figure>)', body_md, re.S):
        img = re.search(r'<img src="([^"]+)"[^>]*alt="([^"]*)"', fig)
        src = img.group(1) if img else None
        if not src:
            continue
        alt = img.group(2) if img else ''
        cap = None
        cm = re.search(r'<figcaption>(.*?)</figcaption>', fig, re.S)
        if cm:
            cap = cm.group(1).strip()
        tags = []
        tm = re.search(r'<!--\s*tags:\s*([^\-->]+)\s*-->', fig)
        if tm:
            tags = [t.strip() for t in tm.group(1).replace('，', ',').split(',') if t.strip()]
        meta[src] = {'alt': alt, 'caption': cap, 'tags': tags}
    # (b) bare markdown images
    for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', body_md):
        url = m.group(2)
        if url not in meta:
            meta[url] = {'alt': m.group(1), 'caption': None, 'tags': []}
    return meta


def attach_image_metadata(draft_body: dict, img_meta: dict, arweave_urls: dict) -> None:
    """Walk the draft ProseMirror body and set alt/caption/tags on image nodes.

    img_meta keys may be local paths or arweave URLs. arweave_urls maps
    absolute local path -> arweave URL.
    """
    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get('type') == 'image2':
            attrs = node.get('attrs') or {}
            src = attrs.get('src', '')
            meta = img_meta.get(src)
            if meta is None and arweave_urls:
                # match by arweave url value
                for k, v in arweave_urls.items():
                    if v == src:
                        meta = img_meta.get(k)
                        break
            if meta:
                if meta.get('alt') and not attrs.get('alt'):
                    attrs['alt'] = meta['alt']
                node['attrs'] = attrs
            node['caption'] = meta.get('caption') if meta else None
            if meta and meta.get('tags'):
                node['tags'] = meta['tags']
        # captionedImage wraps image2; caption may live there too
        if node.get('type') == 'captionedImage':
            inner = node.get('content') or []
            if inner and inner[0].get('type') == 'image2':
                src = inner[0].get('attrs', {}).get('src', '')
                meta = img_meta.get(src)
                if meta is None and arweave_urls:
                    for k, v in arweave_urls.items():
                        if v == src:
                            meta = img_meta.get(k)
                            break
                if meta and meta.get('caption'):
                    node['caption'] = meta['caption']
        for c in (node.get('content') or []):
            if isinstance(c, dict):
                walk(c)
    body = draft_body.get('draft_body')
    if isinstance(body, str):
        doc = json.loads(body)
        for n in doc.get('content', []):
            walk(n)
        draft_body['draft_body'] = json.dumps(doc)

_MD_ARTIFACTS = [
    (r'\*\*[^*\n]+\*\*',                    'unrendered bold **text**'),
    (r'(?<!\*)\*(?!\s)[^*\n]+(?<!\s)\*(?!\*)', 'unrendered italic *text*'),
    (r'(?m)^#{1,6} \S',                     'unrendered heading # text'),
    (r'!\[[^\]]*\]\(',                       'unrendered image ![alt](url)'),
    (r'(?<!\])\[[^\]]+\]\(https?://',        'unrendered link [text](url)'),
    (r'(?m)^-{3,}$',                         'unrendered hr ---'),
]


def verify_post(api, post_id: int, expected_title: str, pub_url: str | None = None):
    """
    Verify a published Substack post.  Prints a report and returns
    (errors, warnings) as lists of strings.

    Checks:
      1. Live body has no assetError nodes (broken images).
      2. All image2 nodes have valid Substack CDN src URLs.
      3. No raw Markdown artifacts survive in text nodes.
      4. Rendered HTML page has image2-inset elements and no missing-image.
    """
    errors, warnings = [], []

    try:
        draft = api.get_draft(post_id)
    except Exception as e:
        return [f'Could not fetch post {post_id}: {e}'], []

    live_title = draft.get('title', '').strip('"\'')
    if expected_title and live_title != expected_title:
        errors.append(f'Title mismatch: expected "{expected_title}", got "{live_title}"')

    body_str = draft.get('body', '{}')
    try:
        body = json.loads(body_str) if isinstance(body_str, str) else body_str
    except json.JSONDecodeError:
        errors.append('Could not parse live body JSON')
        return errors, warnings

    asset_errors, image2_srcs, text_parts = [], [], []

    def _walk(node):
        if not isinstance(node, dict):
            return
        t = node.get('type', '')
        if t == 'assetError':
            asset_errors.append(node.get('attrs', {}).get('url', '(unknown)'))
        elif t == 'image2':
            src = node.get('attrs', {}).get('src', '')
            if src:
                image2_srcs.append(src)
        elif t == 'text':
            text_parts.append(node.get('text', ''))
        for child in node.get('content', []):
            _walk(child)

    _walk(body)

    if asset_errors:
        errors.append(f'{len(asset_errors)} assetError node(s): {asset_errors}')

    for src in image2_srcs:
        if 'substack-post-media' not in src and 'substackcdn' not in src:
            warnings.append(f'Image not on Substack CDN: {src[:80]}')

    all_text = ' '.join(text_parts)
    for pattern, desc in _MD_ARTIFACTS:
        if re.search(pattern, all_text):
            warnings.append(f'Possible unrendered markdown: {desc}')

    if pub_url:
        try:
            import urllib.request as _ur
            req = _ur.Request(pub_url, headers={'User-Agent': 'Mozilla/5.0'})
            with _ur.urlopen(req, timeout=15) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            inset_count = html.count('image2-inset')
            missing_count = html.count('missing-image')
            if missing_count:
                errors.append(f'{missing_count} missing-image placeholder(s) in rendered HTML')
            if image2_srcs and inset_count == 0:
                warnings.append('No image2-inset elements in rendered HTML')
            print(f'[verify] Rendered HTML: {inset_count} image(s), {missing_count} broken')
        except Exception as e:
            warnings.append(f'Could not fetch rendered HTML: {e}')

    print(f'[verify] API body: {len(image2_srcs)} image2 node(s), '
          f'{len(asset_errors)} assetError(s)')

    if errors:
        print('[verify] ERRORS:')
        for e in errors:
            print(f'  ERROR: {e}')
    if warnings:
        print('[verify] WARNINGS:')
        for w in warnings:
            print(f'  WARN:  {w}')
    if not errors and not warnings:
        print('[verify] PASS -- text, images, and rendering look correct')

    return errors, warnings

# ── Auth ─────────────────────────────────────────────────────────────────────

def _extract_brave_cookies() -> dict:
    """
    Decrypt Substack session cookies from Brave's macOS SQLite cookie store.

    Brave uses AES-CBC (v10 format) with a key derived from the macOS Keychain
    entry "Brave Safe Storage" via PBKDF2-HMAC-SHA1.  The encrypted blob has
    the layout:  b'v10' + 16-byte metadata + ciphertext.  The first decrypted
    block is garbage (unknown IV); valid data starts at plaintext byte 16.
    """
    try:
        from Crypto.Cipher import AES
    except ImportError:
        return {}

    db_path = Path.home() / 'Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies'
    if not db_path.exists():
        return {}

    try:
        result = subprocess.run(
            ['security', 'find-generic-password', '-s', 'Brave Safe Storage', '-w'],
            capture_output=True, text=True, check=True,
        )
        raw_key = result.stdout.strip().encode('utf-8')
    except subprocess.CalledProcessError:
        return {}

    key = hashlib.pbkdf2_hmac('sha1', raw_key, b'saltysalt', 1003, dklen=16)
    IV  = b' ' * 16

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        tmp = f.name
    try:
        shutil.copy2(str(db_path), tmp)
        conn = sqlite3.connect(tmp)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, encrypted_value FROM cookies "
            "WHERE host_key='.substack.com' AND name IN ('substack.sid','substack.lli')"
        )
        out = {}
        for name, enc_val in cursor.fetchall():
            if not enc_val or enc_val[:3] != b'v10':
                continue
            ct = enc_val[19:]
            cipher = AES.new(key, AES.MODE_CBC, IV)
            dec = cipher.decrypt(ct)
            pad = dec[-1]
            if 1 <= pad <= 16:
                dec = dec[:-pad]
            value = dec[16:].decode('utf-8', errors='replace')
            if '�' not in value:
                out[name] = value
        conn.close()
        return out
    except Exception:
        return {}
    finally:
        Path(tmp).unlink(missing_ok=True)


def _save_cookies(cookies: dict) -> None:
    """Save cookies to disk, skipping any values with non-Latin-1 chars."""
    clean = {}
    for k, v in cookies.items():
        try:
            v.encode('latin-1')
            clean[k] = v
        except (UnicodeEncodeError, AttributeError):
            pass
    if clean:
        COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        COOKIES_PATH.write_text(json.dumps(clean, indent=2))


def get_api() -> Api:
    """Return authenticated Api instance, loading cookies from file or Brave."""
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)

    if COOKIES_PATH.exists():
        print(f'[auth] Loading cookies from {COOKIES_PATH}')
        try:
            api = Api(cookies_path=str(COOKIES_PATH), publication_url=PUB_URL)
            api.get_user_id()
            print('[auth] Session valid.')
            return api
        except Exception as e:
            print(f'[auth] Cookies invalid ({e}), extracting from Brave...')
            COOKIES_PATH.unlink(missing_ok=True)

    print('[auth] Extracting session cookies from Brave...')
    cookies = _extract_brave_cookies()
    if cookies:
        _save_cookies(cookies)
        print(f'[auth] Saved {len(cookies)} cookies from Brave to {COOKIES_PATH}')
        api = Api(cookies_path=str(COOKIES_PATH), publication_url=PUB_URL)
        try:
            api.get_user_id()
            print('[auth] Brave session valid.')
            return api
        except Exception as e:
            print(f'[auth] Brave cookies invalid: {e}')

    sys.exit(
        '[auth] Could not authenticate. Make sure Brave is logged in to Substack '
        'and try again. (API login requires CAPTCHA and is not supported.)'
    )

# ── Cover image ───────────────────────────────────────────────────────────────

def find_cover_image(body_md: str, essay_dir: Path, override: Path | None) -> Path | None:
    if override and override.exists():
        return override
    # Use first image in body
    m = re.search(r'!\[[^\]]*\]\(([^)]+)\)', body_md)
    if m:
        path = m.group(1)
        if not path.startswith(('http://', 'https://')):
            p = (essay_dir / path).resolve()
            if p.exists():
                return p
    return None

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--essay', required=True,
                    help='Path to essay HTML or MD (slug inferred from directory name)')
    ap.add_argument('--cover', help='Cover image path (default: first image in essay)')
    ap.add_argument('--section', default=SECTION_NAME,
                    help=f'Substack section name (default: {SECTION_NAME})')
    ap.add_argument('--send-email', action='store_true',
                    help='Send to email subscribers (default: web-only)')
    ap.add_argument('--arweave', action='store_true',
                    help='Upload inline images to Arweave (https://arweave.net/{txId}) '
                         'and link them from the post, instead of uploading to Substack S3. '
                         'Alt text is taken from the markdown image alt; captions/tags from '
                         '<figcaption> and <!-- tags: ... --> comments when present.')
    ap.add_argument('--arweave-paid', action='store_true',
                    help='With --arweave: allow uploading files over the 100 KiB free '
                         'tier (consumes Turbo credits). By default oversized images are '
                         'refused and skipped.')
    ap.add_argument('--image-metadata', help='Optional YAML file mapping image path -> '
                    '{alt, caption, tags} to attach to inline images.')
    ap.add_argument('--dry-run', action='store_true',
                    help='Build draft body and print summary without posting')
    args = ap.parse_args()

    # Resolve essay paths
    essay_path = Path(args.essay).resolve()
    essay_dir  = essay_path.parent
    slug       = essay_dir.name
    md_path    = essay_dir / f'{slug}.md'

    if not md_path.exists():
        sys.exit(f'[ERROR] Markdown source not found: {md_path}')

    # Load frontmatter
    fm    = load_fm(md_path)
    title = fm.get('title', '').strip('"\'')
    subtitle = fm.get('subtitle', '').strip('"\'') if fm.get('subtitle') else ''

    if not title:
        sys.exit('[ERROR] No title in frontmatter.')

    # Check if already published on Substack
    for entry in (fm.get('published_at') or []):
        if entry.get('platform') == 'substack' and entry.get('url'):
            print(f'[SKIP] Already published to Substack: {entry["url"]}')
            print('       Remove the substack entry from published_at to re-publish.')
            sys.exit(0)

    # Parse body markdown
    raw_md = md_path.read_text(encoding='utf-8')
    if raw_md.startswith('---'):
        parts = raw_md.split('---', 2)
        body_md = parts[2].strip() if len(parts) >= 3 else raw_md
    else:
        body_md = raw_md

    # Keep an un-rewritten copy for image-metadata parsing (paths intact).
    body_md = preprocess_markdown(body_md, essay_dir)
    body_md_orig = body_md

    # --arweave: upload local inline images to Arweave and link them.
    arweave_urls = {}
    if getattr(args, 'arweave', False):
        print('[arweave] Uploading inline images to Arweave...')
        body_md, arweave_urls = arweave_rewrite_images(
            body_md, essay_dir, paid=getattr(args, 'arweave_paid', False))
        print(f'[arweave] {len(arweave_urls)} image(s) rewritten to arweave.net URLs')

    cover_path = find_cover_image(body_md, essay_dir,
                                  Path(args.cover).resolve() if args.cover else None)

    print(f'Essay   : {slug}')
    print(f'Title   : {title}')
    print(f'Subtitle: {subtitle or "(none)"}')
    print(f'Section : {args.section}')
    print(f'Cover   : {cover_path or "(none)"}')
    print(f'Send    : {"email + web" if args.send_email else "web only"}')
    print(f'Arweave : {"yes" if getattr(args, "arweave", False) else "no"}')

    if args.dry_run:
        print('\n[DRY RUN] Draft body preview (first 500 chars of processed markdown):')
        print(body_md[:500])
        return

    # Authenticate
    api = get_api()
    user_id = api.get_user_id()
    print(f'[auth] User ID: {user_id}')

    # Upload cover image
    cover_url = None
    if cover_path:
        print(f'[image] Uploading cover: {cover_path.name} ...')
        try:
            result = api.get_image(str(cover_path))
            cover_url = result.get('url')
            print(f'[image] Cover URL: {cover_url}')
        except Exception as e:
            print(f'[image] Cover upload failed: {e}')

    # Build Post
    post = Post(
        title=title,
        subtitle=subtitle,
        user_id=user_id,
        audience='everyone',
        write_comment_permissions='everyone',
    )

    # Set section
    try:
        sections = api.get_sections()
        post.set_section(args.section, sections)
        print(f'[section] Set to: {args.section}')
    except Exception as e:
        print(f'[section] Warning -- could not set section: {e}')

    # Parse markdown body (api passed for local image upload)
    print('[content] Parsing markdown and uploading inline images...')
    post.from_markdown(body_md, api=api)

    # Attach alt / caption / tags to image nodes (from figures / metadata)
    img_meta = parse_image_metadata(body_md_orig)
    if getattr(args, 'image_metadata', None):
        _meta = _load_image_metadata_yaml(args.image_metadata)
        img_meta.update(_meta)
        print(f'[metadata] loaded {len(_meta)} entry/ies from {args.image_metadata}')

    # Create draft
    draft_body = post.get_draft()
    attach_image_metadata(draft_body, img_meta, arweave_urls)
    if cover_url:
        draft_body['cover_image'] = cover_url
        # Inject cover as first image in the body for full-width display.
        # cover_image alone only sets the thumbnail; Substack renders the
        # first captionedImage in the body as the hero cover.
        import json
        body_doc = json.loads(draft_body['draft_body'])
        cover_node = {
            "type": "captionedImage",
            "content": [{
                "type": "image2",
                "attrs": {
                    "src": cover_url,
                    "fullscreen": False,
                    "imageSize": "normal",
                    "height": 600,
                    "width": 1200,
                    "resizeWidth": 728,
                    "bytes": None,
                    "alt": None,
                    "title": None,
                    "type": None,
                    "href": None,
                    "belowTheFold": False,
                    "internalRedirect": None
                }
            }]
        }
        body_doc['content'].insert(0, cover_node)
        draft_body['draft_body'] = json.dumps(body_doc)

    print('[draft] Creating draft...')
    draft = api.post_draft(draft_body)
    draft_id = draft.get('id')
    if not draft_id:
        sys.exit(f'[ERROR] Draft creation failed: {draft}')
    print(f'[draft] Created draft ID: {draft_id}')

    # Prepublish check
    print('[publish] Running prepublish check...')
    try:
        api.prepublish_draft(draft_id)
    except Exception as e:
        print(f'[publish] Prepublish warning: {e}')

    # Publish
    print('[publish] Publishing...')
    result = api.publish_draft(
        draft_id,
        send=args.send_email,
        share_automatically=False,
    )
    print(f'[publish] Done. Response: {result}')

    # Fetch URL and update frontmatter
    print('[url] Fetching published URL...')
    pub_url = None
    url_script = Path(__file__).parent / 'substack-post-url'
    if url_script.exists():
        r = subprocess.run([sys.executable, str(url_script), slug],
                           capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode != 0:
            print(f'[url] Warning: {r.stderr.strip()}')
        else:
            # Extract URL from the script's stdout
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith('https://') and 'substack.com' in line:
                    pub_url = line
                    break
    else:
        pub_url = f'https://{PUBLICATION}.substack.com/p/{slug}'
        print(f'[url] Likely URL: {pub_url}')

    # Verify the published post
    print('\n[verify] Verifying published post...')
    verify_post(api, draft_id, title, pub_url)

    # Refresh saved cookies after a successful run
    _save_cookies(api._session.cookies.get_dict())


if __name__ == '__main__':
    main()
