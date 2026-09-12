"""
Tests for publish/src/signal-assemble-full.py

The full-post message must contain the essay title, the plaintext body,
and the contact page link at the end; the hero image path is written to
--images-file.
"""
import sys
from pathlib import Path

from conftest import load_script


def _write_essay(content_dir: Path, slug: str, body: str, title: str = 'Test Essay',
                 extra_fm: str = '') -> Path:
    """Write an essay with frontmatter + body; return the .md path."""
    path = content_dir / slug / f'{slug}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntype: long-form\ntitle: "{title}"\ndate: 2026-01-01\n'
        f'published_at: []\n{extra_fm}---\n\n{body}\n',
        encoding='utf-8',
    )
    return path


def _run(mod, slug, tmp_path, monkeypatch, *extra_args):
    """Run the assembler CLI against a tmp CONTENT_DIR; return exit code."""
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['signal-assemble-full.py', slug, *extra_args])
    return mod.main()


def test_full_contains_title_body_and_contact(capsys, tmp_path, monkeypatch):
    mod = load_script('signal-assemble-full.py')
    slug = '2026-01-01-full-essay'
    body = ('First paragraph of the essay.\n\n'
            'Second paragraph with **bold** and [a link](https://example.com/x).\n')
    _write_essay(tmp_path, slug, body, title='The Full Title')

    assert _run(mod, slug, tmp_path, monkeypatch) == 0
    out = capsys.readouterr().out

    assert out.startswith('The Full Title\n')
    assert 'First paragraph of the essay.' in out
    assert 'Second paragraph with bold and a link (https://example.com/x).' in out
    assert out.rstrip().endswith('Contact: https://archerships.com/contact.html')


def test_full_writes_hero_image_path(tmp_path, monkeypatch):
    mod = load_script('signal-assemble-full.py')
    slug = '2026-01-01-full-hero'
    hero = tmp_path / slug / 'img' / 'hero.webp'
    hero.parent.mkdir(parents=True)
    hero.write_bytes(b'fake-image')
    _write_essay(tmp_path, slug, 'Intro paragraph.\n',
                 extra_fm=f'hero_image: img/hero.webp\n')

    imgs_file = tmp_path / 'imgs.txt'
    assert _run(mod, slug, tmp_path, monkeypatch, '--images-file', str(imgs_file)) == 0
    assert imgs_file.read_text().strip() == str(hero.resolve())


def test_full_missing_essay_fails(capsys, tmp_path, monkeypatch):
    mod = load_script('signal-assemble-full.py')

    assert _run(mod, 'nonexistent-slug', tmp_path, monkeypatch) == 1
    assert 'ERROR: essay not found' in capsys.readouterr().err


def test_full_collects_html_img_inline_images(capsys, tmp_path, monkeypatch):
    """Inline images wrapped in raw <figure><img>...</figure> must be
    collected for attachment (they previously only matched markdown ![]())."""
    mod = load_script('signal-assemble-full.py')
    slug = '2026-01-01-figure-essay'
    img1 = tmp_path / slug / 'img' / 'a.jpg'
    img2 = tmp_path / slug / 'img' / 'b.png'
    img1.parent.mkdir(parents=True)
    img1.write_bytes(b'fake-a')
    img2.write_bytes(b'fake-b')
    body = ('Intro paragraph.\\n\\n'
            '<figure>\\n<img src="img/a.jpg" alt="Diagram A" width="400">\\n'
            '<figcaption>Diagram A caption.</figcaption>\\n</figure>\\n\\n'
            '<figure>\\n<img src="img/b.png" alt="Diagram B">\\n'
            '<figcaption>Diagram B caption.</figcaption>\\n</figure>\\n')
    _write_essay(tmp_path, slug, body)

    capsys.readouterr()  # flush
    imgs_file = tmp_path / 'imgs.txt'
    assert _run(mod, slug, tmp_path, monkeypatch, '--images-file', str(imgs_file)) == 0
    out = capsys.readouterr().out

    lines = imgs_file.read_text().strip().splitlines()
    assert [Path(l) for l in lines] == [img1.resolve(), img2.resolve()]
    # Raw HTML must not leak into the plaintext body; captions remain.
    assert '<figure>' not in out and '<img' not in out and '<figcaption>' not in out
    assert '[IMAGE: img/a.jpg]' in out and '[IMAGE: img/b.png]' in out
    # Captions survive (allowing 80-col wrapping between words).
    import re as _re
    flat = _re.sub(r'\s+', ' ', out)
    assert 'Diagram A caption.' in flat and 'Diagram B caption.' in flat


def test_full_converts_citekeys_to_bracketed_numbers(capsys, tmp_path, monkeypatch):
    """Pandoc [@citekey] references must become sequential bracketed numbers
    in the body text, matching the rendered HTML's footnote numbering."""
    mod = load_script('signal-assemble-full.py')
    slug = '2026-01-01-cites'
    body = ('First claim. [@monticelloMostContestedApartment2019a]\\n\\n'
            'Second claim. [@salazarLimitedSFHousing2026]\\n\\n'
            'Grouped. [@colburnHomelessnessHousingProblem2022; '
            '@pewcharitabletrustsHowHousingCosts2023]\\n')
    _write_essay(tmp_path, slug, body, title='Cited')

    capsys.readouterr()
    assert _run(mod, slug, tmp_path, monkeypatch) == 0
    out = capsys.readouterr().out

    assert '[@monticelloMostContestedApartment2019a]' not in out
    assert '[@salazarLimitedSFHousing2026]' not in out
    assert '[@colburnHomelessnessHousingProblem2022]' not in out
    assert 'First claim. [1]' in out
    assert 'Second claim. [2]' in out
    assert 'Grouped. [3] [4]' in out


def test_full_pdf_mode_is_compact_card(tmp_path, monkeypatch, capsys):
    """--pdf mode emits only Title / Description / URL / Contact (no full
    body, no images, no footnote markers) and writes a single .pdf path."""
    import signal_arweave
    mod = load_script('signal-assemble-full.py')
    slug = '2026-01-01-pdf-card'
    desc = 'A one-line description used in the card.'
    _write_essay(tmp_path, slug,
                 'Full body prose with a [@key2025] citation and inline ![x](img/a.jpg).\n',
                 title='The Card Title', extra_fm=f'description: "{desc}"\n')

    # Avoid running real wkhtmltopdf/gs in the unit test; stub the renderer
    # but honor out_name so the title-derived filename is genuinely tested.
    def _fake_render(html_path, title_hint='essay', out_name=None):
        if out_name:
            return Path('/tmp') / out_name
        return Path('/tmp/out.pdf')
    monkeypatch.setattr(signal_arweave, 'render_pdfa2b', _fake_render)

    capsys.readouterr()
    imgs_file = tmp_path / 'imgs.txt'
    pdffile = tmp_path / 'pdf.txt'
    assert _run(mod, slug, tmp_path, monkeypatch,
                '--images-file', str(imgs_file), '--pdf',
                '--pdf-file', str(pdffile)) == 0
    out = capsys.readouterr().out

    assert out.startswith('The Card Title\n')
    assert desc in out
    assert f'https://archerships.com/essays/{slug}.html' in out
    assert 'Contact: https://archerships.com/contact.html' in out
    # No full body prose, no images, no raw citekeys in the card.
    assert 'Full body prose' not in out
    assert '[IMAGE:' not in out
    assert '[@key2025]' not in out
    # The card's images-file holds only the hero (none set here -> empty).
    assert imgs_file.read_text().strip() == ''
    # The PDF path is written to --pdf-file, named from the title slug.
    pdf_lines = [l for l in pdffile.read_text().splitlines() if l]
    assert len(pdf_lines) == 1 and pdf_lines[0].endswith('.pdf')
    assert 'the-card-title.pdf' in pdf_lines[0]
