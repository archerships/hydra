#!/Users/crasch/av/venv/hydra/bin/python3
"""
signal-assemble-announce.py -- Assemble a Signal announcement from an essay.

Reads SLUG/SLUG.md and builds the compact card matching the full-post
doc-mode card (title, description, the archerships.com post URL, and the
contact page link) -- EXACTLY the same text as the full card, minus the
"Look for an epub in the following message" line. The hero image
(frontmatter hero_image) is resolved to an absolute path and written to
--images-file for attachment.

Usage:
  signal-assemble-announce.py SLUG [--images-file FILE]

Outputs:
  stdout:   card text (title, description, post URL, contact link)
  --images-file FILE: writes the hero image absolute path (if any), one per line
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

import signal_arweave


CONTENT_DIR = Path.home() / 'av' / 'doc' / 'posts'
SITE_URL = 'https://archerships.com/essays'
CONTACT_URL = 'https://archerships.com/contact.html'

_FM_RE = re.compile(r'^---\n(.*?)\n---\n', re.DOTALL)


def hero_image_path(post_dir: Path, fm_text: str) -> Optional[Path]:
    """Resolve the frontmatter hero_image to an absolute path, if it exists."""
    m = re.search(r'^hero_image:\s*["\']?([^\s"\']+)', fm_text, re.M)
    if not m:
        return None
    p = (post_dir / m.group(1).strip()).resolve()
    return p if p.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser(description='Assemble Signal announcement text')
    ap.add_argument('slug', help='Essay slug (directory under ~/av/doc/posts/)')
    ap.add_argument('--images-file', help='Write the hero image absolute path here (one per line)')
    ap.add_argument('--arweave', action='store_true',
                    help='Source the hero image from Arweave (download + transcode to JPEG)')
    args = ap.parse_args()

    post_dir = CONTENT_DIR / args.slug
    md_path = post_dir / f'{args.slug}.md'
    if not md_path.exists():
        print(f'ERROR: essay not found: {md_path}', file=sys.stderr)
        return 1

    raw = md_path.read_text(encoding='utf-8')
    m = _FM_RE.match(raw)
    if not m:
        print('ERROR: no frontmatter', file=sys.stderr)
        return 1
    fm_text = m.group(1)

    title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', fm_text, re.M)
    title = title_m.group(1).strip() if title_m else args.slug

    # Card text must match the full-post doc-mode card (title / description /
    # URL / contact) EXACTLY, minus the "Look for an epub in the following
    # message" line that the full assembler appends.
    description = signal_arweave.extract_folded_fm(fm_text, 'description')
    # Drop the site's trailing "-- archerships" brand footer, mirroring the
    # full card so the two messages read identically.
    description = re.sub(r'--\s*archerships\s*$', '', description.strip(),
                       flags=re.I).strip()

    hero = hero_image_path(post_dir, fm_text)
    if args.arweave and hero:
        hero = signal_arweave.fetch_attachment(hero)

    out = [title]
    if description:
        out.append('')
        out.append(description)
    out.append('')
    out.append(f'{SITE_URL}/{args.slug}.html')
    out.append('')
    out.append(f'Contact: {CONTACT_URL}')
    print('\n'.join(out))

    if args.images_file:
        Path(args.images_file).write_text(
            f'{hero}\n' if hero else '',
            encoding='utf-8')

    return 0


if __name__ == '__main__':
    sys.exit(main())
