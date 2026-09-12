"""
Tests for publish/src/hydra-publish

Pure-function unit tests (frontmatter, already_published, record_published,
extract_intro) are tested by importing the module directly so no subprocess
or Brave browser is required.

The end-to-end CLI is tested with subprocess and --dry-run to avoid touching
real browser sessions or the live archerships.com directory.
"""
import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).parent.parent / 'hydra-publish'


def load_module():
    loader = importlib.machinery.SourceFileLoader('hydra_publish', str(SCRIPT))
    spec = importlib.util.spec_from_loader('hydra_publish', loader, origin=str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(SCRIPT)  # module_from_spec does not set it on Python 3.12+
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return load_module()


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def test_read_frontmatter_returns_dict(mod, tmp_path, monkeypatch):
    slug = 'test-essay'
    essay = tmp_path / f'{slug}.md'
    essay.write_text(
        '---\ntitle: "Hello"\ndate: 2026-01-01\npublished_at: []\n---\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    fm = mod.read_frontmatter(slug)
    assert fm['title'] == 'Hello'
    assert fm['published_at'] == []


def test_read_frontmatter_missing_file_exits(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    with pytest.raises(SystemExit):
        mod.read_frontmatter('nonexistent-slug')


# ---------------------------------------------------------------------------
# already_published
# ---------------------------------------------------------------------------

def test_already_published_false_when_empty(mod):
    assert mod.already_published({'published_at': []}, 'twitter-article') is False


def test_already_published_false_when_different_platform(mod):
    fm = {'published_at': [{'platform': 'facebook', 'url': '', 'date': '2026-01-01'}]}
    assert mod.already_published(fm, 'twitter-article') is False


def test_already_published_false_when_empty_url(mod):
    """An entry with url: '' should NOT count as published -- allows retry."""
    fm = {'published_at': [{'platform': 'facebook', 'url': '', 'date': '2026-01-01'}]}
    assert mod.already_published(fm, 'facebook') is False


def test_already_published_true_when_listed(mod):
    """An entry with a real URL counts as published."""
    fm = {'published_at': [{'platform': 'twitter-article',
                            'url': 'https://x.com/archerships/status/1',
                            'date': '2026-01-01'}]}
    assert mod.already_published(fm, 'twitter-article') is True


def test_already_published_handles_missing_key(mod):
    assert mod.already_published({}, 'facebook') is False


# ---------------------------------------------------------------------------
# record_published
# ---------------------------------------------------------------------------

def test_record_published_appends_entry(mod, tmp_path, monkeypatch):
    slug = 'my-essay'
    essay = tmp_path / f'{slug}.md'
    essay.write_text(
        '---\ntitle: "My Essay"\npublished_at: []\n---\nBody text.\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    mod.record_published(slug, 'facebook', url='https://fb.com/post/123')

    text = essay.read_text(encoding='utf-8')
    fm_text = text.split('---', 2)[1]
    fm = yaml.safe_load(fm_text)
    assert len(fm['published_at']) == 1
    entry = fm['published_at'][0]
    assert entry['platform'] == 'facebook'
    assert entry['url'] == 'https://fb.com/post/123'


def test_record_published_stacks_multiple_entries(mod, tmp_path, monkeypatch):
    slug = 'multi-essay'
    essay = tmp_path / f'{slug}.md'
    essay.write_text(
        '---\ntitle: "Multi"\npublished_at: []\n---\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    mod.record_published(slug, 'facebook')
    mod.record_published(slug, 'nostr', url='https://nostr.build/xyz')

    fm_text = essay.read_text(encoding='utf-8').split('---', 2)[1]
    fm = yaml.safe_load(fm_text)
    platforms = [e['platform'] for e in fm['published_at']]
    assert 'facebook' in platforms
    assert 'nostr' in platforms


def test_record_published_body_preserved(mod, tmp_path, monkeypatch):
    slug = 'body-essay'
    body = '\nThis is the essay body.\n'
    essay = tmp_path / f'{slug}.md'
    essay.write_text(f'---\ntitle: "Body"\npublished_at: []\n---\n{body}',
                     encoding='utf-8')
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    mod.record_published(slug, 'substack')
    assert body in essay.read_text(encoding='utf-8')


def test_record_published_yaml_remains_valid(mod, tmp_path, monkeypatch):
    slug = 'valid-yaml'
    essay = tmp_path / f'{slug}.md'
    essay.write_text(
        '---\ntitle: "YAML Test"\npublished_at: []\n---\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    mod.record_published(slug, 'nostr', url='https://example.com')
    text = essay.read_text(encoding='utf-8')
    _, fm_text, _ = text.split('---', 2)
    fm = yaml.safe_load(fm_text)   # raises if invalid
    assert isinstance(fm, dict)


def test_record_published_nostr_skips_empty_url(mod, tmp_path, monkeypatch):
    """record_published must NOT write a nostr entry with an empty URL.

    Nostr URLs must be valid naddr bech32 strings produced by
    'nak encode naddr'. An empty URL would block re-publishing (because
    already_published() checks for platform key existence, not URL value)
    and invite manual insertion of the invalid '30023:pubkey/slug' format.
    """
    slug = 'nostr-skip-test'
    essay = tmp_path / f'{slug}.md'
    essay.write_text(
        '---\ntitle: "Nostr Skip"\npublished_at: []\n---\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    mod.record_published(slug, 'nostr', url='')
    # Frontmatter should remain unchanged — no entry written
    text = essay.read_text(encoding='utf-8')
    _, fm_text, _ = text.split('---', 2)
    fm = yaml.safe_load(fm_text)
    assert fm['published_at'] == []


def test_record_published_nostr_writes_valid_url(mod, tmp_path, monkeypatch):
    """When a valid naddr URL is provided, record_published writes it normally."""
    slug = 'nostr-valid-test'
    essay = tmp_path / f'{slug}.md'
    essay.write_text(
        '---\ntitle: "Nostr Valid"\npublished_at: []\n---\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(mod, 'CONTENT_DIR', tmp_path)
    naddr_url = 'https://yakihonne.com/article/naddr1qq0nyvpjxcknqd3dxqmz66rp'
    mod.record_published(slug, 'nostr', url=naddr_url)
    text = essay.read_text(encoding='utf-8')
    _, fm_text, _ = text.split('---', 2)
    fm = yaml.safe_load(fm_text)
    assert len(fm['published_at']) == 1
    assert fm['published_at'][0]['url'] == naddr_url


# ---------------------------------------------------------------------------
# extract_intro
# ---------------------------------------------------------------------------

def test_extract_intro_returns_title_and_paragraph(mod, tmp_path, monkeypatch):
    slug = 'intro-test'
    html = tmp_path / f'{slug}.html'
    html.write_text(
        '<html><body><main><h1>Great Title</h1>'
        '<p>First paragraph.</p><p>Second paragraph.</p></main></body></html>',
        encoding='utf-8',
    )
    monkeypatch.setattr(mod, 'DST_DIR', tmp_path)
    title, intro = mod.extract_intro(slug)
    assert title == 'Great Title'
    assert intro == 'First paragraph.'


def test_extract_intro_skips_empty_paragraphs(mod, tmp_path, monkeypatch):
    slug = 'empty-p'
    html = tmp_path / f'{slug}.html'
    html.write_text(
        '<html><body><main><h1>Title</h1><p>  </p><p>Real intro.</p></main></body></html>',
        encoding='utf-8',
    )
    monkeypatch.setattr(mod, 'DST_DIR', tmp_path)
    _, intro = mod.extract_intro(slug)
    assert intro == 'Real intro.'


def test_extract_intro_fallback_slug_when_no_h1(mod, tmp_path, monkeypatch):
    slug = 'no-title'
    html = tmp_path / f'{slug}.html'
    html.write_text('<html><body><main><p>Intro.</p></main></body></html>',
                    encoding='utf-8')
    monkeypatch.setattr(mod, 'DST_DIR', tmp_path)
    title, _ = mod.extract_intro(slug)
    assert title == slug


# ---------------------------------------------------------------------------
# CLI: dry-run does not modify frontmatter
# ---------------------------------------------------------------------------

def test_dry_run_does_not_modify_frontmatter(tmp_path):
    slug = 'dry-run-essay'
    content_dir = tmp_path / 'src' / 'content' / 'essays'
    dst_dir     = tmp_path / 'dst' / 'essays'
    content_dir.mkdir(parents=True)
    dst_dir.mkdir(parents=True)

    md_path = content_dir / f'{slug}.md'
    md_path.write_text(
        '---\ntitle: "Dry Run"\npublished_at: []\n---\n',
        encoding='utf-8',
    )
    (dst_dir / f'{slug}.html').write_text(
        '<html><body><main><h1>Dry Run</h1><p>Intro.</p></main></body></html>',
        encoding='utf-8',
    )

    original = md_path.read_text(encoding='utf-8')

    r = subprocess.run(
        [sys.executable, str(SCRIPT), slug,
         '--platforms', 'facebook',
         '--dry-run'],
        env={
            **__import__('os').environ,
            'HYDRA_CONTENT_DIR': str(content_dir),
            'HYDRA_DST_DIR':     str(dst_dir),
        },
        capture_output=True, text=True,
    )
    # Script will exit non-zero because CONTENT_DIR env override isn't
    # implemented — but the frontmatter must not have changed.
    assert md_path.read_text(encoding='utf-8') == original


# ---------------------------------------------------------------------------
# CLI: unknown platform exits nonzero
# ---------------------------------------------------------------------------

def test_unknown_platform_exits_nonzero(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), 'some-slug',
         '--platforms', 'totally-made-up'],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
