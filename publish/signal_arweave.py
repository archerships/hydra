#!/Users/crasch/av/venv/hydra/bin/python3
"""
signal_arweave.py -- Shared helpers for Signal post assemblers.

Two concerns:

1. Arweave sourcing: resolve a local essay image to its permanent
   https://arweave.net/{txId} (from deployed.json, falling back to a live
   upload via arweave-upload.mjs), download the bytes to a temp local
   file, and transcode WebP -> JPEG so Signal renders it reliably.

2. Plaintext normalization: convert Pandoc citekey references to
   bracketed numbers, mirroring the rendered HTML's numbered footnotes.

Used by signal-assemble-full.py and signal-assemble-announce.py.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

PROJ       = Path.home() / 'av' / 'prj' / 'archerships.com'
LOCKFILE   = PROJ / 'deployed.json'
ARWEAVE_NET = 'https://arweave.net'
UPLOADER   = Path.home() / 'av' / 'bin' / 'archerships' / 'arweave-upload.mjs'
_TX_RE     = re.compile(r'https://arweave\.net/([A-Za-z0-9_-]+)')
# Pandoc citeproc citekey reference: [@key]  or  [@key1; @key2] (grouped).
_CITE_RE   = re.compile(r'\[@([^\]]+)\]')

# PDF/A-2b tooling. PDFA_def.ps must reference an absolute srgb.icc path or
# ghostscript cannot resolve the ICC profile and the conversion aborts.
_HAS_PDFA = None  # lazy probe

# Track temp files so the caller can clean them up.
_downloaded: list[Path] = []


def _tmp_path(suffix: str) -> Path:
    """Create a temp file path, honoring SIGNAL_TMPDIR so hydra-signal can
    clean up the arweave downloads when it removes its own scratch dir."""
    base = os.environ.get('SIGNAL_TMPDIR')
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=suffix, dir=base)
        os.close(fd)
        return Path(name)
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return Path(name)


def convert_citations(text: str) -> str:
    """Convert Pandoc citekey references to bracketed numbers.

    Each citation occurrence (including each key inside a grouped
    [@a; @b] cite) is numbered sequentially in body order, mirroring the
    rendered HTML's numbered footnotes: [@monticello...] -> [1], etc.
    Keys may carry locators/suffixes (e.g. [@key, p. 3]); only the
    @key token is used, the rest is dropped.
    """
    counter = [0]

    def repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        # Split grouped citations on ';'. The pattern already matched a
        # leading '@' (captured group is the bare key), so every non-empty
        # part is a citation.
        parts = [p.strip() for p in inner.split(';')]
        nums = []
        for part in parts:
            if not part:
                continue
            counter[0] += 1
            nums.append(f'[{counter[0]}]')
        if not nums:
            return m.group(0)   # empty cite; leave as-is
        return ' '.join(nums)

    return _CITE_RE.sub(repl, text)


def sanitize_text(text: str) -> str:
    """Strip stray symbols that corrupt URLs and layout.

    Removes return-arrow glyphs (↵ U+21A9, ↩ U+21A9 family) and their
    variation/emoji selectors and zero-width chars -- these get embedded
    next to footnote URLs and break copy-paste.
    """
    out = []
    for ch in text:
        cp = ord(ch)
        if cp in (0x21A9, 0x21AA, 0x21B5):        # arrows
            continue
        if cp == 0xFE0F or cp == 0xFE0E:          # variation selectors
            continue
        if cp in (0x200B, 0x200C, 0x200D, 0x2060):  # zero-width / joiners
            continue
        out.append(ch)
    return ''.join(out)


def wrap_paragraphs(text: str, width: int = 80) -> str:
    """Wrap prose paragraphs to `width` columns, keeping URLs and
    [IMAGE: ...] markers atomic (never broken across lines).

    Blank lines separate paragraphs; a paragraph that is itself a URL is
    left unwrapped.
    """
    import textwrap
    # Protect atomic tokens from internal wrapping.
    placeholders: dict[str, str] = {}
    counter = [0]

    def stash(m: re.Match) -> str:
        counter[0] += 1
        tok = f'__TOK{counter[0]}__'
        placeholders[tok] = m.group(0)
        return tok

    # Protect [IMAGE: ...] markers and bare/inline URLs.
    guarded = re.sub(r'\[IMAGE:[^\]]*\]', stash, text)
    guarded = re.sub(r'https?://\S+', stash, guarded)

    def unstash(line: str) -> str:
        for tok, orig in placeholders.items():
            line = line.replace(tok, orig)
        return line

    lines = []
    for para in guarded.split('\n\n'):
        para = para.strip()
        if not para:
            continue
        if re.match(r'^https?://\S+$', para):
            lines.append(unstash(para))
            lines.append('')
            continue
        filled = textwrap.fill(para, width=width,
                               break_long_words=False,
                               break_on_hyphens=False)
        lines.append(unstash(filled))
        lines.append('')
    return '\n'.join(lines).rstrip()


def slugify(text: str) -> str:
    """Slugify a title for a filename: lowercase, non-alnum -> dash."""
    s = sanitize_text(text or '').lower()
    s = re.sub(r"[^a-z0-9]+", '-', s).strip('-')
    return s or 'essay'


def extract_folded_fm(frontmatter: str, key: str) -> str:
    """Extract a (possibly multi-line folded) YAML frontmatter scalar.

    Handles single-line values and indented continuation lines (folded
    style), stripping surrounding quotes and collapsing continuation
    whitespace. Returns '' if the key is absent.
    """
    lines = frontmatter.splitlines()
    started = False
    parts: list[str] = []
    for line in lines:
        s = line.rstrip('\n')
        if not started:
            m = re.match(rf'^{key}:\s*(.*)$', s)
            if not m:
                continue
            started = True
            parts.append(m.group(1))
            continue
        # A new top-level key ends the block.
        if re.match(r'^\S+:', s):
            break
        # Continuation line (folded): strip one-level indent + leading space.
        indent_len = len(s) - len(s.lstrip())
        val = s[indent_len:] if indent_len else s
        parts.append(val)
    if not started:
        return ''
    raw = ' '.join(p.strip() for p in parts).strip()
    # Strip matching surrounding quotes.
    if len(raw) >= 2:
        first: str = raw[0]
        last: str = raw[-1]
        if first == last and first in ('"', "'"):
            raw = raw[1:-1]
    return raw


def _load_lockfile() -> dict:
    try:
        return json.loads(LOCKFILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def arweave_url_for(local_path: Path) -> str | None:
    """Return the https://arweave.net/{txId} for a local image, or None.

    Looks up deployed.json by basename (keys are 'essays/img/live/<name>').
    If not found, uploads the local file via arweave-upload.mjs (free tier,
    auto-shrink) and returns the resulting URL.
    """
    local_path = local_path.resolve()
    if not local_path.exists():
        return None
    base = local_path.name
    lock = _load_lockfile()
    for key, val in lock.items():
        if key.endswith('/' + base) or key == base:
            if isinstance(val, dict) and val.get('txId'):
                return f'{ARWEAVE_NET}/{val["txId"]}'
            if isinstance(val, str):
                return f'{ARWEAVE_NET}/{val}'
    # Not in lockfile -> upload live (free tier, auto-shrink).
    if UPLOADER.exists():
        try:
            r = subprocess.run(['node', str(UPLOADER), str(local_path)],
                               capture_output=True, text=True, timeout=180)
            m = _TX_RE.search(r.stdout)
            if m:
                return f'{ARWEAVE_NET}/{m.group(1)}'
        except Exception:
            pass
    return None


def download_to_temp(url: str) -> Path | None:
    """Download an arweave URL to a temp local file; returns the path."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if not data:
            return None
        tmp = _tmp_path('.img')
        tmp.write_bytes(data)
        _downloaded.append(tmp)
        return tmp
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None


def transcode_to_jpeg(src: Path) -> Path:
    """Convert a WebP (or any Pillow-readable) image to a JPEG temp file.

    PNG is left as-is when it has transparency; otherwise converted.
    Returns the path to the attachment-ready file.
    """
    from PIL import Image
    try:
        im = Image.open(src)
        if src.suffix.lower() == '.png' and im.mode in ('RGBA', 'LA', 'P'):
            # Keep PNG for transparency; Signal renders PNG fine.
            return src
        jpg = _tmp_path('.jpg')
        im.convert('RGB').save(jpg, 'JPEG', quality=92)
        _downloaded.append(jpg)
        return jpg
    except Exception:
        return src


def fetch_attachment(local_path: Path) -> Path:
    """Resolve a local image to an attachment-ready temp file.

    If the image is on Arweave, downloads the permanent copy and
    transcodes to JPEG; otherwise returns the local path unchanged.
    """
    url = arweave_url_for(local_path)
    if url:
        dl = download_to_temp(url)
        if dl:
            return transcode_to_jpeg(dl)
    return local_path


# ── PDF/A-2b ───────────────────────────────────────────────────────────────
def _find_pdfa_def() -> Path | None:
    """Locate PDFA_def.ps and return one whose ICCProfile is absolute."""
    candidates = [
        Path('/opt/homebrew/Cellar/ghostscript/10.07.0/share/ghostscript/lib/PDFA_def.ps'),
    ]
    import glob
    if not any(p.exists() for p in candidates):
        for c in glob.glob('/opt/homebrew/Cellar/ghostscript/*/share/ghostscript/lib/PDFA_def.ps'):
            candidates.append(Path(c))
    if not any(p.exists() for p in candidates):
        return None
    src = next(p for p in candidates if p.exists())

    icc = glob.glob('/opt/homebrew/Cellar/ghostscript/*/share/ghostscript/iccprofiles/srgb.icc')
    if not icc:
        return None
    # Emit a temp PDFA_def.ps with the absolute srgb.icc path (bare
    # '(srgb.icc)' is not on gs's lib search path and aborts conversion).
    dst = _tmp_path('.ps')
    dst.write_text(src.read_text().replace('(srgb.icc)', f'({icc[0]})'))
    return dst


def _compose_cover_bytes(data: bytes, title: str, author: str) -> bytes:
    """Overlay the title (single block-capital line near the top, above the
    characters' heads) and the author (smaller, lower-right) onto a cover/hero
    image. Reused so the EPUB cover and thumbnail carry title + author."""
    from PIL import Image, ImageDraw, ImageFont
    import io
    im = Image.open(io.BytesIO(data)).convert('RGB')
    W, H = im.size
    def font(fp, size):
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            return ImageFont.load_default()
    def text_w(f, s):
        try:
            d = ImageDraw.Draw(im)
            b = d.textbbox((0, 0), s, font=f)
            return b[2] - b[0]
        except Exception:
            return 0
    fb = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
    fk = '/System/Library/Fonts/Supplemental/Arial Black.ttf'
    draw = ImageDraw.Draw(im)

    # Title on ONE line near the TOP (above Spooner & Dee's heads), block caps,
    # on a dark semi-transparent banner behind it (as in the first build).
    maxw = W - max(24, int(W * 0.03))
    tt = title.upper()
    tf = font(fk, int(H * 0.085))
    if text_w(tf, tt) > maxw:            # shrink to fit a single line
        while tf.size > 18 and text_w(tf, tt) > maxw:
            try:
                tf = font(fk, tf.size - 2)
            except Exception:
                break
    tw = text_w(tf, tt)
    # Place the banner a bit below the top edge so it isn't clipped by the
    # image boundary.
    tly = max(int(H * 0.05), 14)          # banner top line, clear of the edge
    # Render the text to a scratch image first and measure its INK bbox, so
    # the banner and text are centered on what is actually drawn (avoiding
    # the all-caps "sits high" problem from descender space in the font bbox).
    def render_text(txt, f):
        tmp = Image.new('RGBA', (max(int(text_w(f, txt)) + 8, 4), int(f.size * 2)), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).text((4, 4), txt, font=f, fill=(255, 255, 255, 255), anchor='la')
        b = tmp.getbbox()
        return tmp, b
    rimg, bbox = render_text(tt, tf)
    th = (bbox[3] - bbox[1]) if bbox else int(tf.size)
    tw = (bbox[2] - bbox[0]) if bbox else tw
    pad_x = int(H * 0.02)
    pad_y = int(H * 0.012)
    # Dark semi-transparent banner behind the title, centered with the text.
    bx = (W - tw) // 2 - pad_x
    by = tly - pad_y
    bw = int(tw) + 2 * pad_x
    bh = th + 2 * pad_y
    opt = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(opt).rounded_rectangle([bx, by, bx + bw - 1, by + bh - 1],
                                          radius=int(th * 0.3), fill=(15, 15, 20, 215))
    # Paste the rendered ink (crop to bbox) centered in the banner.
    ink = rimg.crop(bbox)
    ink_x = bx + pad_x
    ink_y = by + pad_y
    opt.paste(ink, (ink_x, ink_y), ink)
    im = Image.alpha_composite(im.convert('RGBA'), opt).convert('RGB')
    draw = ImageDraw.Draw(im)

    # Author in the LOWER RIGHT corner, smaller (same family), with the SAME
    # dark rounded banner behind it as the title.
    af = font(fb, max(20, int(H * 0.05)))
    at = author.upper()
    def render_text2(txt, f):
        tmp = Image.new('RGBA', (max(int(text_w(f, txt)) + 8, 4), int(f.size * 2)), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).text((4, 4), txt, font=f, fill=(255, 255, 255, 255), anchor='la')
        b = tmp.getbbox()
        return tmp, b
    r2, b2 = render_text2(at, af)
    a_w = (b2[2] - b2[0]) if b2 else text_w(af, at)
    a_h = (b2[3] - b2[1]) if b2 else int(af.size)
    a_px = int(H * 0.015)
    a_py = int(H * 0.008)
    a_bx = W - a_w - a_px - max(12, int(W * 0.03))
    a_by = H - a_h - a_py - max(8, int(H * 0.02))
    opt2 = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(opt2).rounded_rectangle(
        [a_bx, a_by, a_bx + a_w + 2 * a_px - 1, a_by + a_h + 2 * a_py - 1],
        radius=int(a_h * 0.3), fill=(15, 15, 20, 215))
    ink2 = r2.crop(b2)
    opt2.paste(ink2, (a_bx + a_px, a_by + a_py), ink2)
    im = Image.alpha_composite(im.convert('RGBA'), opt2).convert('RGB')

    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return buf.getvalue()


def _pngify_epub_media(epub_path: Path, title: str = 'essay',
                       author: str = 'archerships') -> None:
    """Convert all WebP media in an EPUB to PNG, fix every reference, and
    normalize the cover page.

    WebP is poorly supported across EPUB readers (and the SVG cover can
    crash strict readers). This rewrites the .epub zip: every media/*.webp
    becomes media/*.png, image references in the XHTML, cover.svg and
    content.opf are re-pointed, broken site-image link wrappers are
    stripped, and the cover becomes a single first page with the title +
    author + hero image. Runs after _fix_epub_xhtml.
    """
    import zipfile
    from PIL import Image
    tmp = epub_path.with_name(epub_path.name + '.pngf')
    with zipfile.ZipFile(epub_path, 'r') as zin:
        # Pre-scan the cover page for the hero image href (the cover media
        # is referenced by cover.xhtml's <image> element).
        hero_href = None
        for n in zin.namelist():
            if (n.endswith('cover.xhtml') or n.endswith('cover.html')):
                m = re.search(r'<image[^>]*xlink:href="([^"]+)"',
                              zin.read(n).decode('utf-8', 'ignore'))
                if m:
                    hero_href = m.group(1)
    with zipfile.ZipFile(epub_path, 'r') as zin, \
         zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            name = info.filename
            data = zin.read(name)
            if 'media/' in name and name.lower().endswith('.webp'):
                png_name = name[:-5] + '.png'
                im = Image.open(__import__('io').BytesIO(data))
                if im.mode in ('RGBA', 'LA', 'P'):
                    im = im.convert('RGBA')
                else:
                    im = im.convert('RGB')
                buf = __import__('io').BytesIO()
                im.save(buf, 'PNG')
                png_bytes = buf.getvalue()
                base = name.rsplit('/', 1)[-1]
                is_cover = bool(hero_href) and base == hero_href.rsplit('/', 1)[-1]
                if is_cover:
                    # Compose the COVER with the (shortened) title on a single
                    # line at top + the credit line lower-right. The title and
                    # author come from this function's params (set by the
                    # caller), so the cover is reproducible for any book.
                    cover_author = author or 'archerships'
                    cover_title = title or 'essay'
                    try:
                        composed = _compose_cover_bytes(png_bytes, cover_title, cover_author)
                    except Exception:
                        composed = png_bytes
                    # Write the composed cover as file23.png
                    c = zipfile.ZipInfo(png_name, date_time=info.date_time)
                    c.compress_type = zipfile.ZIP_DEFLATED
                    zout.writestr(c, composed)
                    # Write the plain hero for the internal first page.
                    p = zipfile.ZipInfo('EPUB/media/hero_plain.png', date_time=info.date_time)
                    p.compress_type = zipfile.ZIP_DEFLATED
                    zout.writestr(p, png_bytes)
                    continue
                info2 = zipfile.ZipInfo(png_name, date_time=info.date_time)
                info2.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(info2, png_bytes)
                continue
            if name.endswith('.xhtml') or name == 'EPUB/content.opf':
                text = data.decode('utf-8')
                text = re.sub(r'([A-Za-z0-9_./-]+)\.webp', r'\1.png', text, flags=re.IGNORECASE)
                # Fix cover/opf webp->png ids + media-types.
                text = re.sub(r'file(\d+)_webp', r'file\1_png', text)
                if name == 'EPUB/content.opf':
                    text = re.sub(r'(media/file\d+\.png)([^>]*?)media-type="image/webp"',
                                  r'\1\2media-type="image/png"', text)
                    # Reader pattern (matches professionally-produced EPUBs like
                    # "When All Else Fails"): the FIRST spine page is the real
                    # COVER image page, then the book opens on the title/body.
                    # Do NOT strip cover/title from the spine (that made Apple
                    # Books open on ch001 whose pandoc `h1 { page-break-before:
                    # always }` forced a BLANK first page). Keep cover_xhtml in
                    # the spine so page 1 = the cover image -- Apple Books shows
                    # it, then the reading flow. The cover-image item still
                    # provides the library thumbnail.

                    # The plain internal hero we add under the title must be a
                    # declared manifest item, or Calibre reports
                    # "Referenced file 'EPUB/media/hero_plain.png' not in
                    # manifest" and drops the image.
                    if 'hero_plain' not in text:
                        text = re.sub(
                            r'(</manifest>)',
                            '<item id="hero_plain" href="media/hero_plain.png" '
                            'media-type="image/png" /></manifest>',
                            text, count=1)
                # Strip broken <a href="img/live/..."> lightbox wrappers: the
                # markdown figures wrapped each image in a link to the SITE
                # path which isn't packaged in the EPUB, so strict readers
                # (Calibre) report each as a missing referenced file and
                # some crash. Keep the inner <img>.
                if name.endswith('.xhtml'):
                    text = re.sub(
                        r'<a\s+href="img/[^"]*"[^>]*>(.*?)</a>',
                        r'\1', text, flags=re.DOTALL | re.IGNORECASE)
                # Replace the pandoc SVG cover wrapper (which readers embed
                # as an inline data:image/svg+xml;base64 blob that some
                # readers, e.g. bookokrat, dump as raw garbage) with a plain
                # title + hero image first page. Extract the image href from
                # the <image>. The cover becomes a plain centered image page
                # (the title + author page is prepended to the main content
                # chapter, so the dedicated cover stays a clean image file).
                if name.endswith('cover.xhtml') or name.endswith('cover.html'):
                    m = re.search(r'<image[^>]*xlink:href="([^"]+)"', text)
                    if m and '<body' in text:
                        href = m.group(1)
                        mbody = re.search(r'<body[^>]*>', text)
                        body_html = (
                            '<div id="cover-image" style="text-align:center;">'
                            f'<img src="{href}" alt="cover" /></div>')
                        text = (text[:mbody.end()] + body_html + '</body></html>') if mbody else text
                    # Do NOT mark the body id="cover" (Calibre then makes it a
                    # pure image splash; it is only a plain image cover page).
                    text = re.sub(r'<body[^>]*id="cover"[^>]*>', '<body>', text)
                # Place the hero image directly under the essay's first
                # heading (ch001), so the first page reads: TITLE, then the
                # HERO image, then the essay body. (The dedicated cover page
                # is a separate full-page hero splash; the essay's own <h1>
                # supplies the title, and the hero sits beneath it.)
                if re.match(r'^EPUB/text/ch\d+\.xhtml$', name) and hero_href \
                        and 'id="hero-under-title"' not in text:
                    mh = re.search(r'(</h1>)', text)
                    if mh:
                        # Internal hero = the PLAIN original (no baked title),
                        # as metadata text carries the credits below the title.
                        # Use a plain <div class="pubmeta">, NOT
                        # <section epub:type="imprint">: Apple Books treats
                        # "imprint" as a reserved publishing semantic and drops its
                        # contents (same class of bug as the <aside> footnotes),
                        # leaving page 1 blank below the title. A plain div
                        # renders in both Apple Books and Calibre.
                        meta = (
                            '<div class="pubmeta">'
                            '<p><strong>Author:</strong> Archer T. Ships</p>'
                            '<p><strong>Publication Date:</strong> 2026-08-26</p>'
                            '<p><strong>Publisher:</strong> Scoria House</p>'
                            '<p><strong>Live URL:</strong> '
                            '<a href="https://archerships.com/essays/2026-06-26-zoning-reform-voter-fears">'
                            'https://archerships.com/essays/2026-06-26-zoning-reform-voter-fears</a></p>'
                            '<p><strong>Copyright:</strong> CC0 1.0 Universal '
                            '(<a href="https://creativecommons.org/publicdomain/zero/1.0/">'
                            'https://creativecommons.org/publicdomain/zero/1.0/</a>)</p>'
                            '</div>'
                            '<div id="hero-under-title" '
                            'style="text-align:center;padding:1em 0;">'
                            '<img src="../media/hero_plain.png" alt="cover" /></div>')
                        text = text[:mh.end()] + meta + text[mh.end():]
                data = text.encode('utf-8')
            zout.writestr(info, data)
    tmp.replace(epub_path)


def _fix_epub_xhtml(epub_path: Path, title: str = 'essay',
                    author: str = 'archerships') -> None:
    """Repair pandoc's EPUB XHTML in place (rewrites the .epub zip).

    Pandoc emits HTML5-style void tags (<img ...>) unself-closed, which is
    invalid in EPUB's XHTML content documents. It also leaves note-style
    footnotes unnumbered (each <aside id="fnN"> lacks a visible numeral)
    and the main content chapter lacks a title page. This rewrites every
    .xhtml entry to self-close void elements, numbers the footnotes, adds a
    Footnotes h1 + nav entry, and prepends a title + author + hero page to
    the top of the first content chapter so Calibre/readers show it (Calibre
    suppresses text inside the dedicated cover page, so the title must live
    in the normal reading flow).
    """
    import zipfile
    _VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img',
             'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    tmp = epub_path.with_name(epub_path.name + '.fixed')
    with zipfile.ZipFile(epub_path, 'r') as zin, \
         zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith('.xhtml'):
                text = data.decode('utf-8')
                # Self-close any void tag that isn't already closed.
                def _close(m: re.Match) -> str:
                    name = m.group(1).lower()
                    if name in _VOID and not re.search(r'/\s*>$', m.group(0)):
                        return m.group(0)[:-1] + ' />'
                    return m.group(0)
                text = re.sub(r'<(\w+)([^<>]*)>', _close, text)
                # Apple Books renders pandoc-style rearnotes (<section
                # class="footnotes"> wrapping <aside epub:type="footnote">) as an
                # EMPTY page -- it drops the <aside> content. Calibre shows them,
                # but Apple Books does not. Convert the note-style footnotes into a
                # plain <ol class="footnotes"> of <li> items, which every reader
                # (Calibre + Apple Books) renders reliably. The body's
                # <a href="#fnN" epub:type="noteref"> links still resolve.
                def _note(m: re.Match) -> str:
                    num = re.sub(r'\D', '', m.group(1)) or ''
                    inner = m.group(2).strip()
                    # Keep the inner <p> so multi-block note bodies render; drop
                    # only the redundant surrounding <p>.
                    inner = inner.replace('<p>', '', 1)
                    if inner.endswith('</p>'):
                        inner = inner[:-4]
                    return (f'<li id="fn{num}" class="fn-item">'
                            f'<span class="fn-num">{num}. </span>{inner}</li>')
                text = re.sub(
                    r'<aside[^>]*role="doc-footnote"[^>]*id="(fn\d+)"[^>]*>\s*<p>(.*?)</p>\s*</aside>',
                    _note, text, flags=re.DOTALL | re.IGNORECASE)
                # Ensure the footnotes section gets an <h1> heading (pandoc
                # emits a bare <section id="footnotes"> with no heading) so it
                # can be a TOC entry -- BEFORE we build the <ol> wrapper, which
                # anchors on that heading.
                if re.search(r'<section[^>]*id="footnotes"[^>]*>', text) and \
                   not re.search(r'<section[^>]*id="footnotes"[^>]*>\s*<h1', text, re.IGNORECASE):
                    text = re.sub(
                        r'(<section[^>]*id="footnotes"[^>]*>)',
                        r'\1<h1 id="footnotes-title">Footnotes</h1>',
                        text, count=1, flags=re.IGNORECASE)
                # Wrap the footnote items in a visible ordered list. The EPUB3
                # spec/Apple Books rearnotes pattern: <section role="doc-endnotes"
                # epub:type="endnotes"><h1>Footnotes</h1><ol><li id="fnN">...
                if re.search(r'<section[^>]*id="footnotes"[^>]*>', text) and \
                   '<ol class="footnotes">' not in text:
                    text = re.sub(
                        r'(<section[^>]*id="footnotes"[^>]*>\s*'
                        r'<h1[^>]*>Footnotes</h1>)(.*?)(</section>)',
                        lambda m: m.group(1) +
                        '<ol class="footnotes">\n' + m.group(2) + '\n</ol>' +
                        m.group(3),
                        text, count=1, flags=re.DOTALL | re.IGNORECASE)
                # If this is nav.xhtml, append a Footnotes entry to the TOC
                # (as a top-level item) so the section appears in the nav.
                if info.filename.endswith('nav.xhtml'):
                    if 'id="toc-li-footnotes"' not in text:
                        text = re.sub(
                            r'(</ol></nav>)',
                            r'<li id="toc-li-footnotes"><a href="text/ch001.xhtml#footnotes">Footnotes</a></li></ol></nav>',
                            text, count=1)
                data = text.encode('utf-8')
            zout.writestr(info, data)
    tmp.replace(epub_path)


def _parse_bibtex(text: str) -> dict[str, dict[str, str]]:
    """Parse a BibTeX file into {key: {field: value}} (lowercased fields).
    Handles nested braces and unbraced numeric values."""
    entries: dict[str, dict[str, str]] = {}
    for m in re.finditer(r'@(\w+)\s*\{\s*([^,]+?)\s*,\s*(.*?)\n\}', text,
                         re.DOTALL):
        key = m.group(2).strip()
        body = m.group(3)
        fields: dict[str, str] = {'@type': m.group(1).lower()}
        # Match each top-level field with brace-aware value extraction.
        i = 0
        while i < len(body):
            fm = re.match(r'\s*(\w+)\s*=\s*', body[i:])
            if not fm:
                i += 1
                continue
            fname = fm.group(1).lower()
            j = i + fm.end()
            if j < len(body) and body[j] == '{':
                depth = 0
                k = j
                while k < len(body):
                    if body[k] == '{':
                        depth += 1
                    elif body[k] == '}':
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                value = body[j + 1:k]
                i = k + 1
            else:
                m2 = re.match(r'([^\s,]+)', body[j:])
                value = m2.group(1) if m2 else ''
                i = j + (m2.end() if m2 else 0)
            fields[fname] = value.strip()
        entries[key] = fields
    return entries


def _fmt_author(author: str) -> str:
    """'Last, First and Last, First' -> 'First Last and First Last'."""
    parts = []
    for a in author.split(' and '):
        a = a.strip()
        if ',' in a:
            last, first = a.split(',', 1)
            parts.append(f'{first.strip()} {last.strip()}')
        else:
            parts.append(a)
    return ' and '.join(parts)


def _clean_bib_value(s: str) -> str:
    """Strip BibTeX capitalization-protection braces ({{X}} or {X} -> X)."""
    s = re.sub(r'\{(\{[^{}]*\})\}', r'\1', s)   # {{X}} -> {X}
    return re.sub(r'\{([^{}]*)\}', r'\1', s)     # {X} -> X


def _format_chicago_note(f: dict[str, str]) -> str:
    """Format a citation in rough Chicago note style from BibTeX fields."""
    def cv(k: str, default: str = '') -> str:
        return _clean_bib_value(f.get(k, default))
    author = _fmt_author(cv('author')) if cv('author') else ''
    title = cv('title')
    year = cv('year', 'n.d.')
    typ = f.get('@type', 'misc')
    if typ in ('article', 'techreport'):
        container = cv('journal') or cv('institution') or cv('publisher')
        out = f'{author}, “{title}”' if author else f'“{title}”'
        if container:
            out += f', <em>{container}</em>'
    else:  # book, misc
        out = f'{author}, <em>{title}</em>' if author else f'<em>{title}</em>'
    if typ == 'techreport':
        num = cv('number') or cv('type')
        if num and 'report' not in num.lower():
            out += f', {num}'
    url = cv('url') or cv('howpublished') or cv('note')
    if url.startswith(('http://', 'https://')):
        out += f', [{url}]({url})'
    out += f', {year}.'
    return out


def _citekeys_to_footnotes(md: str, bib: dict[str, dict[str, str]]) -> str:
    """Convert Pandoc [@citekey] refs into native markdown [^N] footnotes.

    Each cited key maps (in order of first appearance) to a sequential
    footnote number; a matching bibliography entry is formatted in Chicago
    note style as the footnote text, and the definitions are appended at the
    end of the document. This avoids citeproc's alphabetized bibliography
    while keeping properly numbered footnotes.
    """
    counter: list[int] = [0]
    order: list[str] = []
    texts: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        nums = []
        # The pattern already matched the leading '@', so each non-empty
        # semicolon-separated part is a citekey (the captured group is the
        # bare key).
        for part in inner.split(';'):
            part = part.strip()
            if not part:
                continue
            key = part
            if key not in texts:
                counter[0] += 1
                n = counter[0]
                order.append(key)
                f = bib.get(key, {})
                texts[key] = _format_chicago_note(f) if f else key
                nums.append(f'[^{n}]')
            else:
                for k, num in zip(order, range(1, counter[0] + 1)):
                    if k == key:
                        nums.append(f'[^{num}]')
                        break
        return ' '.join(nums)

    md = re.sub(r'\[@([^\]]+)\]', repl, md)
    if order:
        defs = '\n\n' + '\n\n'.join(
            f'[^{i + 1}]: {texts[key]}' for i, key in enumerate(order))
        md = md.rstrip() + defs + '\n'
    return md


def _section_markers_to_headings(md: str) -> str:
    """Convert the essay's numbered section-marker lines into markdown
    headings so pandoc can build an EPUB TOC.

    Section markers look like '1. WHY CARE...', '1.1. What Harms...',
    '1.1.1. Homelessness'. Threaded through render-post.py these become the
    <h2>/<h3>/<h4> headings on the site, but the raw markdown has no ATX
    heading markers, so pandoc's EPUB nav is empty. This rewrites:
      - a dotted marker (2+ segments, e.g. 1.1 or 1.1.1) -> heading
      - a bare 'N.' followed by ALL-CAPS text -> chapter heading (h2)
      and leaves numbered BULLETS (bare 'N.' + title-case text) untouched.
    Level: 1 segment->h2, 2->h3, 3->h4.
    """
    out_lines = []
    for raw in md.splitlines():
        s = raw.rstrip()
        m = re.match(r'^(\d+(?:\.\d+)+)\.\s+(.*)$', s)        # 1.1 | 1.1.1
        if m:
            segs = len(m.group(1).split('.'))
            level = min(segs + 1, 6)                          # 2, 3, 4
            out_lines.append('#' * level + ' ' + m.group(2))
            continue
        ch = re.match(r'^(\d+)\.\s+([A-Z][A-Z0-9 ,’:\-\&\/]+\??)$', s)  # ALL-CAPS chapter
        if ch:
            out_lines.append('## ' + ch.group(2))
            continue
        out_lines.append(s)
    return '\n'.join(out_lines)


def render_epub(md_path: Path, title: str = 'essay',
                cover_path: Path | None = None) -> Path | None:
    """Render an essay markdown file to an EPUB3 document via pandoc.

    Runs pandoc from the essay directory so relative img/ paths resolve,
    converts the essay's numbered section markers into headings (for the
    EPUB TOC), converts @citekeys into native numbered markdown footnotes
    (resolved against ~/Zotero/bibliography.bib in Chicago note style -- no
    citeproc, so no alphabetized bibliography), attaches an optional cover
    image, repairs the XHTML void-tag self-closing pandoc skips, and names
    the output from title. Returns the temp .epub path or None.
    """
    if not md_path.exists():
        return None
    pandoc_bin = shutil.which('pandoc')
    if not pandoc_bin:
        return None
    src = md_path.read_text(encoding='utf-8')
    # Drop YAML frontmatter.
    body = re.sub(r'^---\n.*?\n---\n', '', src, flags=re.DOTALL, count=1)
    bib = {}
    bibpath = Path.home() / 'Zotero' / 'bibliography.bib'
    if bibpath.exists():
        bib = _parse_bibtex(bibpath.read_text(encoding='utf-8'))
    body = _citekeys_to_footnotes(body, bib)
    body = _section_markers_to_headings(body)
    tmp_md = _tmp_path('.md')
    tmp_md.write_text(body, encoding='utf-8')
    out_epub = _tmp_path('.epub')
    cmd = [pandoc_bin, str(tmp_md), '-t', 'epub3',
           '--metadata', f'title={sanitize_text(title)}',
           '--metadata', 'author=archerships',
           '--toc-depth=4',
           '-o', str(out_epub)]
    if cover_path and cover_path.exists():
        cmd += ['--epub-cover-image', str(cover_path)]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=180, cwd=str(md_path.parent))
    if r.returncode != 0 or not out_epub.exists():
        return None
    try:
        _fix_epub_xhtml(out_epub, title=title, author='archerships')
        _pngify_epub_media(out_epub, title=title, author='archerships')
    except Exception:
        pass   # fail soft; return pandoc's output if repair fails
    return out_epub


def pdfa2b_available() -> bool:
    """True if wkhtmltopdf + ghostscript + an immutable PDFA_def are present."""
    global _HAS_PDFA
    if _HAS_PDFA is None:
        _HAS_PDFA = bool(
            shutil.which('wkhtmltopdf')
            and shutil.which('gs')
            and _find_pdfa_def() is not None
        )
    return _HAS_PDFA


def _html_with_raster_images(html_path: Path,
                             uniform_width: int = 600) -> Path | None:
    """Return a temp HTML copy whose local images are raster formats.

    wkhtmltopdf does not decode WebP in <img>, silently skipping those
    images (only JPEG/PNG embed). This rewrites every local image src to a
    resized PNG (via Pillow) at a UNIFORM pixel width, so all figures share
    the same rendered width while keeping their native aspect ratio and
    higher resolution. It also strips footnote-back backlinks (the '↵'
    glyph that corrupts copy-pasted footnote URLs in the PDF).
    """
    from PIL import Image
    if not html_path.exists():
        return None
    html = html_path.read_text(encoding='utf-8')
    base = html_path.parent
    rendered: dict[str, str] = {}

    def conv(m: re.Match) -> str:
        tag = m.group(0)
        src = m.group(1)
        if src.startswith(('http://', 'https://', 'data:')):
            return tag
        p = (base / src).resolve() if not src.startswith('/') else Path(src)
        if not p.exists():
            return tag
        if src in rendered:
            return tag.replace(f'src="{src}"', f'src="{rendered[src]}"')
        try:
            im = Image.open(p)
            im.load()
            # Preserve transparency in the PNG output.
            if im.mode in ('RGBA', 'LA', 'P'):
                im = im.convert('RGBA')
            else:
                im = im.convert('RGB')
            # Resize to a uniform width (drop the upscale path: only enlarge
            # if smaller; the live images are all >= the source refs).
            w = uniform_width
            ratio = w / im.width
            h = max(1, round(im.height * ratio))
            im = im.resize((w, h), Image.Resampling.LANCZOS)
            png = _tmp_path('.png')
            im.save(png, 'PNG')
            rendered[src] = str(png)
            # Override the tag's own width so every figure renders the same
            # width regardless of its original HTML width attribute.
            tag = re.sub(
                r'\s(width|height)="\d+"', '', tag, flags=re.IGNORECASE)
            tag = tag.replace(f'src="{src}"',
                              f'src="{rendered[src]}" style="width:{w}px;height:auto;"')
            return tag
        except Exception:
            return tag

    render_html = _replace_img_srcs(html, conv)
    out = render_html.read_text(encoding='utf-8')

    # Drop footnote-back backlinks (the '↩' adjacent to each footnote URL).
    out = re.sub(
        r'<a[^>]*class="footnote-back"[^>]*>.*?</a>',
        '', out, flags=re.DOTALL | re.IGNORECASE)

    # wkhtmltopdf clips the auto-generated <ol> markers (the footnote
    # numbers) against the page's left margin, cropping 2-digit numbers
    # like '56.' on the left. Replace the <ol> auto-numbering with explicit
    # in-flow numbers (from each li id="fnN") so they can never be clipped.
    def _manual_list(m: re.Match) -> str:
        inner = m.group(1)
        def _li(mm: re.Match) -> str:
            fid = mm.group(1)
            body = mm.group(2).strip()
            num = re.sub(r'\D', '', fid) or ''
            # Force single spacing: collapse any paragraph margins inside
            # the note body and give the row only a hairline gap below.
            def _zero_p(mp: re.Match) -> str:
                attrs = mp.group(1)
                if 'style=' in attrs:
                    # inject margin/padding into existing style
                    attrs = re.sub(r'style="([^"]*)"',
                                   r'style="margin:0;padding:0;\1"', attrs)
                else:
                    attrs += ' style="margin:0;padding:0;"'
                return f'<p{attrs}>'
            body = re.sub(r'<p([^>]*)>', _zero_p, body, flags=re.IGNORECASE)
            body = re.sub(r'\s*</p>\s*<p', '</p><p', body)
            return (f'<table cellpadding="0" cellspacing="0" '
                    f'style="margin:0 0 .12em 0;border-collapse:collapse;">'
                    f'<tr><td style="width:2.6em;vertical-align:top;'
                    f'padding:0;white-space:nowrap;">{num}.</td>'
                    f'<td style="vertical-align:top;padding:0;">{body}</td>'
                    f'</tr></table>')
        items = re.sub(
            r'<li id="(fn\d+)"[^>]*>(.*?)</li>',
            _li, inner, flags=re.DOTALL | re.IGNORECASE)
        return '<div style="margin:1em 0;">' + items + '</div>'
    out = re.sub(
        r'<section[^>]*class="footnotes[^>]*>.*?<ol>(.*?)</ol>.*?</section>',
        _manual_list, out, flags=re.DOTALL | re.IGNORECASE)

    render_html.write_text(out, encoding='utf-8')
    return render_html


def _replace_img_srcs(html: str, conv) -> Path:
    """Apply conv to every local <img src="..."> in the html string."""
    src_re = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"[^>]*>', re.IGNORECASE)
    out = src_re.sub(conv, html)
    tmp = _tmp_path('.html')
    tmp.write_text(out, encoding='utf-8')
    return tmp


def render_pdfa2b(html_path: Path, title_hint: str = 'essay',
                  out_name: str | None = None) -> Path | None:
    """Render an essay HTML file to a PDF/A-2b document.

    Pipeline: wkhtmltopdf (HTML -> PDF) then ghostscript -dPDFA=2
    (PDF -> PDF/A-2b). Returns the temp PDF path (suffix .pdf) or None on
    failure. The PDF lifespan is owned by the caller (SIGNAL_TMPDIR).
    `out_name` (optional) names the PDF file (basename only) so the
    downloaded attachment carries a readable name.
    """
    if not html_path.exists():
        return None
    pdfa_def = _find_pdfa_def()
    if not pdfa_def:
        return None
    wtm = shutil.which('wkhtmltopdf')
    gsm = shutil.which('gs')
    if not wtm or not gsm:
        return None
    wt: str = wtm
    gs: str = gsm

    # Convert WebP/non-raster local images to PNG and render that copy so
    # wkhtmltopdf embeds every figure (it silently drops WebP).
    render_html = _html_with_raster_images(html_path)
    if render_html is None:
        return None

    raw_pdf = _tmp_path('.raw.pdf')
    # Run wkhtmltopdf from the HTML's own directory so the (rewritten)
    # absolute image paths resolve without local-file issues. --outline-depth
    # builds a section-heading bookmark tree in the PDF; ghostscript's
    # PDF/A pass keeps the outline (it strips only link annotations).
    r = subprocess.run(
        [wt, '--enable-local-file-access', '--page-size', 'Letter',
         '--outline-depth', '6',
         '--title', title_hint[:120], str(render_html), str(raw_pdf)],
        capture_output=True, text=True, timeout=180, cwd=str(render_html.parent))
    if r.returncode != 0 or not raw_pdf.exists():
        return None

    out_pdf = _tmp_path('.pdf')
    r2 = subprocess.run(
        [gs, '-dPDFA=2', '-dBATCH', '-dNOPAUSE', '-dNOOUTERSAVE',
         '-sColorConversionStrategy=RGB', '-dProcessColorModel=/DeviceRGB',
         '-sDEVICE=pdfwrite', '-dPDFACompatibilityPolicy=1',
         '-sOutputFile=' + str(out_pdf),
         '-f', str(pdfa_def), '-f', str(raw_pdf)],
        capture_output=True, text=True, timeout=300)
    if r2.returncode != 0 or not out_pdf.exists():
        return None
    if out_name:
        # Rename to the requested readable basename (same dir as temp).
        final = out_pdf.with_name(slugify(out_name[:-4]) + '.pdf') if out_name.endswith('.pdf') \
            else out_pdf.with_name(slugify(out_name) + '.pdf')
        if final != out_pdf:
            try:
                final.write_bytes(out_pdf.read_bytes())
                out_pdf = final
            except OSError:
                pass
    return out_pdf


def cleanup() -> None:
    """Remove temp files created during this run."""
    for p in _downloaded:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    _downloaded.clear()
