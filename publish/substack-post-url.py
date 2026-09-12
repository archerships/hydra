#!/Users/crasch/av/venv/hydra/bin/python3
"""
substack-post-url.py -- Find a published Substack post URL by essay title,
verify it is live, and update the essay's published_at frontmatter.

Usage:
  substack-post-url.py <essay-slug>
  substack-post-url.py 2026-06-06-halter-ai-cow-collar

The essay slug is the directory name under ~/av/doc/posts/.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from essay_frontmatter import load_fm, set_platform_field
from hydra_paths import CONTENT_DIR, RENDERER

PUBLICATION   = os.environ.get('SUBSTACK_PUBLICATION', 'archerships')
API_BASE      = f'https://{PUBLICATION}.substack.com/api/v1'

HEADERS = {'User-Agent': 'Mozilla/5.0'}


def fetch_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def find_post_by_title(title: str, limit: int = 20) -> dict | None:
    """Search recent Substack posts for one matching title."""
    url = f'{API_BASE}/posts?limit={limit}&offset=0'
    posts = fetch_json(url)
    title_lower = title.lower().strip()
    for post in posts:
        if post.get('title', '').lower().strip() == title_lower:
            return post
    return None


def verify_url(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers=HEADERS, method='HEAD')
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    slug = sys.argv[1].strip('/')
    slug_dir = CONTENT_DIR / slug
    md_path  = slug_dir / f'{slug}.md'

    if not md_path.exists():
        sys.exit(f'[ERROR] Essay not found: {md_path}')

    fm = load_fm(md_path)
    title = fm.get('title', '').strip('"\'')
    if not title:
        sys.exit('[ERROR] No title found in frontmatter.')

    print(f'Essay : {slug}')
    print(f'Title : {title}')
    print(f'Searching Substack ({PUBLICATION}.substack.com) ...')

    post = find_post_by_title(title)
    if not post:
        sys.exit(f'[ERROR] No Substack post found matching title: {title!r}\n'
                 f'        Try increasing limit or check the publication slug.')

    canonical_url = post.get('canonical_url') or \
                    f"https://{PUBLICATION}.substack.com/p/{post.get('slug','')}"
    post_id       = str(post.get('id', ''))
    post_date     = (post.get('post_date') or '')[:10]

    print(f'Found : {canonical_url}')
    print(f'ID    : {post_id}')
    print(f'Date  : {post_date}')

    print('Verifying URL is live ...')
    if verify_url(canonical_url):
        print(f'[OK] 200 -- post is live.')
    else:
        print(f'[WARN] URL did not return 200 -- may still be propagating.')

    print('Updating frontmatter ...')
    set_platform_field(md_path, 'substack',
                       url=canonical_url,
                       id=post_id,
                       date=post_date or fm.get('date', ''))
    print(f'[OK] published_at updated in {md_path.name}')

    # Re-render so "Also published at" link appears in the HTML
    print('Re-rendering essay ...')
    import subprocess
    result = subprocess.run(
        ['python3', str(RENDERER), str(md_path)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print('[OK] Essay re-rendered.')
    else:
        print(f'[WARN] Renderer returned non-zero:\n{result.stderr}')

    print(f'\nDone. Substack URL: {canonical_url}')


if __name__ == '__main__':
    main()
