#!/Users/crasch/av/venv/hydra/bin/python3
"""
signal-assemble-full.py -- Assemble a full Signal post from an essay.

Reads SLUG/SLUG.md, strips frontmatter, converts the body to plain text,
resolves the hero + inline images to absolute paths, and closes with a
link to the archerships.com contact page.

Inline images may appear as Markdown ![alt](path) OR as raw HTML
<figure><img src="..."><figcaption>...</figcaption></figure>. Both forms
are collected and attached; the raw HTML is stripped from the body text.

Usage:
  signal-assemble-full.py SLUG [--images-file FILE] [--arweave] [--pdf]

Outputs:
  stdout:   plain-text post body (title + body, markdown stripped, contact
            page link appended)
  --images-file FILE: writes one absolute image path per line (hero first,
                      then inline images in body order). Omit to skip images.
  --arweave: resolve each image to its permanent Arweave URL, download it,
             and transcode WebP->JPEG before writing the attachment path.
  --pdf:     instead of images, render the essay's built HTML to a single
             PDF/A-2b document and write that one path to --images-file.

Citekeys:  Pandoc-style [@citekey] references in the body are converted to
           bracketed numbers ([1], [2], ...) in body order, mirroring the
           rendered HTML's numbered footnotes.
"""

import argparse

import re
import sys
from pathlib import Path

import signal_arweave



CONTENT_DIR = Path.home() / 'av' / 'doc' / 'posts'
CONTACT_URL = 'https://archerships.com/contact.html'

_FM_RE = re.compile(r'^---\n.*?\n---\n', re.DOTALL)
_IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
# Raw HTML <img src="..."> and <figure>...</figure> wrappers.
_HTML_IMG_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"[^>]*>', re.IGNORECASE)
_HTML_FIGURE_RE = re.compile(r'</?(?:figure|figcaption)\b[^>]*>', re.IGNORECASE)


def strip_markdown(m: str) -> str:
    """Convert essay markdown + inline HTML to readable plain text (ASCII-safe)."""
    # Headings
    m = re.sub(r'^#{1,6}\s+', '', m, flags=re.M)
    # Horizontal rules
    m = re.sub(r'^---+$', '', m, flags=re.M)
    # Footnotes inline: [^N] -> (note N)
    m = re.sub(r'\[\^(\d+)\]', r' [note \1]', m)
    # Footnote definitions: keep the text, drop the marker
    m = re.sub(r'^\[\^(\d+)\]:\s*', r'[note \1] ', m, flags=re.M)
    # Images -> [IMAGE: alt] so text stays readable
    m = re.sub(r'!\[([^\]]*)\]\([^)]+\)', lambda m_: f'[IMAGE: {m_.group(1)}]' if m_.group(1) else '[IMAGE]', m)
    # Raw HTML <img> -> [IMAGE: alt]
    m = re.sub(_HTML_IMG_RE, lambda m_: f'[IMAGE: {m_.group(1)}]' if m_.group(1) else '[IMAGE]', m)
    # Strip <figure>/<figcaption> wrapper tags (keep caption text)
    m = _HTML_FIGURE_RE.sub('', m)
    # Links -> text (URL)
    m = _LINK_RE.sub(lambda m_: f'{m_.group(1).strip()} ({m_.group(2)})' if m_.group(1).strip() else m_.group(2), m)
    # Bold/italic/emphasis
    m = re.sub(r'(\*\*|__)(.*?)\1', r'\2', m)
    m = re.sub(r'(\*|_)([^*_]+)\1', r'\2', m)
    # Inline code / code fences
    m = re.sub(r'`{1,3}([^`]+)`{1,3}', r'\1', m)
    # Blockquotes
    m = re.sub(r'^>\s?', '', m, flags=re.M)
    # List markers
    m = re.sub(r'^\s*[-*+]\s+', '  * ', m, flags=re.M)
    # Collapse 3+ blank lines to 1
    m = re.sub(r'\n{3,}', '\n\n', m)
    return m.strip()


def collect_images(body_md: str, post_dir: Path) -> list[Path]:
    """Resolve hero + inline image paths to absolute paths (existing only).

    Handles both Markdown ![alt](path) and raw HTML <img src="path">.
    """
    images: list[Path] = []

    # Inline images from body, in order (markdown + HTML)
    for m in _IMG_RE.finditer(body_md):
        rel = m.group(2).strip()
        if rel.startswith(('http://', 'https://', 'data:')):
            continue
        p = (post_dir / rel).resolve()
        if p.exists() and p not in images:
            images.append(p)
    for m in _HTML_IMG_RE.finditer(body_md):
        rel = m.group(1).strip()
        if rel.startswith(('http://', 'https://', 'data:')):
            continue
        p = (post_dir / rel).resolve()
        if p.exists() and p not in images:
            images.append(p)

    # Hero image from frontmatter (prepended)
    fm_text = re.match(r'^---\n(.*?)\n---\n', Path(post_dir / f'{post_dir.name}.md').read_text(encoding='utf-8'), re.DOTALL)
    if fm_text:
        hero = re.search(r'^hero_image:\s*["\']?([^\s"\']+)', fm_text.group(1), re.M)
        if hero:
            hp = (post_dir / hero.group(1).strip()).resolve()
            if hp.exists() and hp not in images:
                images.insert(0, hp)
    return images


def main() -> int:
    ap = argparse.ArgumentParser(description='Assemble full Signal post text + images')
    ap.add_argument('slug', help='Essay slug (directory under ~/av/doc/posts/)')
    ap.add_argument('--images-file', help='Write absolute image paths here, one per line')
    ap.add_argument('--arweave', action='store_true',
                    help='Source images from Arweave (download + transcode to JPEG)')
    docg = ap.add_mutually_exclusive_group()
    docg.add_argument('--pdf', action='store_true',
                      help='Emit the essay as a PDF/A-2b (sent as a separate message)')
    docg.add_argument('--epub', action='store_true',
                      help='Emit the essay as an EPUB3 (sent as a separate message)')
    ap.add_argument('--pdf-file', help='Write the PDF path here (one per line; --pdf mode)')
    ap.add_argument('--epub-file', help='Write the EPUB path here (one per line; --epub mode)')
    args = ap.parse_args()

    post_dir = CONTENT_DIR / args.slug
    md_path = post_dir / f'{args.slug}.md'
    if not md_path.exists():
        print(f'ERROR: essay not found: {md_path}', file=sys.stderr)
        return 1

    raw = md_path.read_text(encoding='utf-8')
    fm = re.match(_FM_RE, raw)
    body = _FM_RE.sub('', raw, count=1).strip()
    if not body:
        print('ERROR: no body content after frontmatter', file=sys.stderr)
        return 1

    # Title from frontmatter
    title = ''
    if fm:
        t = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', fm.group(0), re.M)
        if t:
            title = t.group(1).strip()

    doc_mode = args.pdf or args.epub
    if doc_mode:
        # Compact card: Title / Description / URL / Contact, plus a document
        # (PDF/A-2b or EPUB3) sent as a separate follow-up message.
        description = ''
        if fm:
            description = signal_arweave.extract_folded_fm(fm.group(0), 'description')
        # Drop the site's trailing "-- archerships" brand footer, if the
        # description field carries one, so the card matches the expected
        # clean description line.
        description = re.sub(r'--\s*archerships\s*$', '', description.strip(), flags=re.I).strip()
        site_url = 'https://archerships.com/essays'
        fmt = 'pdf' if args.pdf else 'epub'
        article = 'an' if fmt == 'epub' else 'a'
        out = [title, '', description, '',
               f'{site_url}/{args.slug}.html', '', f'Contact: {CONTACT_URL}',
               '', f'Look for {article} {fmt} in the following message.']
        print('\n'.join(x for x in out if x is not None))
    else:
        parts = []
        if title:
            parts.append(title)
            parts.append('=' * min(len(title), 60))
        body_text = signal_arweave.wrap_paragraphs(
            strip_markdown(signal_arweave.sanitize_text(signal_arweave.convert_citations(body))))
        parts.append(body_text)
        parts.append(f'Contact: {CONTACT_URL}')
        print('\n\n'.join(parts))

    if args.images_file:
        if doc_mode:
            # Card message: hero image (collect_images() puts it at index 0)
            # + the compact text. The PDF/EPUB is written to its own file for
            # a SEPARATE follow-up send, so the Signal client surfaces it as a
            # clear downloadable document.
            hero_paths = collect_images(body, post_dir)[:1]
            if args.arweave:
                hero_paths = [signal_arweave.fetch_attachment(p) for p in hero_paths]
            title_hint = title or args.slug
            slug = signal_arweave.slugify(title_hint)
            if args.pdf:
                doc_path = signal_arweave.render_pdfa2b(
                    post_dir / f'{args.slug}.html', title_hint=title_hint,
                    out_name=slug + '.pdf')
                out_file = args.pdf_file
                label = 'PDF/A-2b'
            else:
                hero_img = hero_paths[0] if hero_paths else None
                doc_path = signal_arweave.render_epub(md_path, title=title_hint,
                                                      cover_path=hero_img)
                if doc_path:
                    # Rename to a readable slug.epub filename (physically copy
                    # the temp file, since Path.with_name only renames the path).
                    named = doc_path.with_name(slug + '.epub')
                    try:
                        named.write_bytes(doc_path.read_bytes())
                        doc_path = named
                    except OSError:
                        pass
                out_file = args.epub_file
                label = 'EPUB'
            if not doc_path:
                print(f'ERROR: {label} render failed for {args.slug}', file=sys.stderr)
                return 1
            write = [str(p) for p in hero_paths]
            if out_file:
                Path(out_file).write_text(f'{doc_path}\n', encoding='utf-8')
        else:
            images = collect_images(body, post_dir)
            if args.arweave:
                images = [signal_arweave.fetch_attachment(p) for p in images]
            write = [str(p) for p in images]
        Path(args.images_file).write_text(
            '\n'.join(write) + ('\n' if write else ''),
            encoding='utf-8')

    return 0


if __name__ == '__main__':
    sys.exit(main())
