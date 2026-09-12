"""
Tests for publish/src/signal-assemble-announce.py

The announcement card must match the full-post doc-mode card (title,
description, the archerships.com post URL, and the contact page link) --
EXACTLY the same text as the full card EXCEPT it must NOT contain the
"Look for an epub in the following message" line. The hero image path is
written to --images-file.
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
    monkeypatch.setattr(sys, 'argv', ['signal-assemble-announce.py', slug, *extra_args])
    return mod.main()


def test_announce_contains_title_description_and_links(capsys, tmp_path, monkeypatch):
    mod = load_script('signal-assemble-announce.py')
    slug = '2026-01-01-test-essay'
    body = ('First paragraph of the essay, the intro.\n\n'
            'Second paragraph, should not appear.\n')
    _write_essay(tmp_path, slug, body, title='The Test Title',
                 extra_fm='description: "A card description."\n')

    assert _run(mod, slug, tmp_path, monkeypatch) == 0
    out = capsys.readouterr().out

    assert out.startswith('The Test Title\n')
    # The card uses the frontmatter description, not the first body paragraph.
    assert 'A card description.' in out
    assert 'First paragraph of the essay, the intro.' not in out
    assert f'https://archerships.com/essays/{slug}.html' in out
    assert 'Contact: https://archerships.com/contact.html' in out
    # No leftover platform block, no EPUB/PDF pointer line.
    assert 'Available on' not in out
    assert 'Look for an epub' not in out
    assert 'Look for a pdf' not in out
    assert 'Second paragraph' not in out


def test_announce_blank_line_between_post_url_and_contact(capsys, tmp_path, monkeypatch):
    mod = load_script('signal-assemble-announce.py')
    slug = '2026-01-01-url-essay'
    _write_essay(tmp_path, slug, 'Only paragraph.\n',
                 extra_fm='description: "Short desc."\n')

    assert _run(mod, slug, tmp_path, monkeypatch) == 0
    out = capsys.readouterr().out

    assert (f'https://archerships.com/essays/{slug}.html\n\n'
            f'Contact: https://archerships.com/contact.html\n') in out


def test_announce_description_brand_footer_stripped(capsys, tmp_path, monkeypatch):
    """The trailing '-- archerships' brand footer in the description is dropped
    so the card matches the full card's clean description line."""
    mod = load_script('signal-assemble-announce.py')
    slug = '2026-01-01-brand-essay'
    _write_essay(tmp_path, slug, 'Body.\n',
                 extra_fm='description: "The demo description -- archerships"\n')

    assert _run(mod, slug, tmp_path, monkeypatch) == 0
    out = capsys.readouterr().out

    assert 'The demo description -- archerships' not in out
    assert 'The demo description' in out


def test_announce_without_description_omits_that_line(capsys, tmp_path, monkeypatch):
    """With no frontmatter description, the card still has title, URL, contact."""
    mod = load_script('signal-assemble-announce.py')
    slug = '2026-01-01-desc-empty'
    _write_essay(tmp_path, slug, 'Body line.\n')

    assert _run(mod, slug, tmp_path, monkeypatch) == 0
    out = capsys.readouterr().out

    assert out.startswith('Test Essay\n')
    assert f'https://archerships.com/essays/{slug}.html' in out
    assert 'Contact: https://archerships.com/contact.html' in out


def test_announce_writes_hero_image_path(tmp_path, monkeypatch):
    mod = load_script('signal-assemble-announce.py')
    slug = '2026-01-01-hero-essay'
    hero = tmp_path / slug / 'img' / 'hero.webp'
    hero.parent.mkdir(parents=True)
    hero.write_bytes(b'fake-image')
    _write_essay(tmp_path, slug, 'Intro paragraph.\n',
                 extra_fm=f'hero_image: img/hero.webp\n')

    imgs_file = tmp_path / 'imgs.txt'
    assert _run(mod, slug, tmp_path, monkeypatch, '--images-file', str(imgs_file)) == 0
    assert imgs_file.read_text().strip() == str(hero.resolve())


def test_announce_without_hero_writes_empty_images_file(tmp_path, monkeypatch):
    mod = load_script('signal-assemble-announce.py')
    slug = '2026-01-01-nohero-essay'
    _write_essay(tmp_path, slug, 'Intro paragraph.\n')

    imgs_file = tmp_path / 'imgs.txt'
    assert _run(mod, slug, tmp_path, monkeypatch, '--images-file', str(imgs_file)) == 0
    assert imgs_file.read_text() == ''


def test_announce_missing_essay_fails(capsys, tmp_path, monkeypatch):
    mod = load_script('signal-assemble-announce.py')

    assert _run(mod, 'nonexistent-slug', tmp_path, monkeypatch) == 1
    assert 'ERROR: essay not found' in capsys.readouterr().err
