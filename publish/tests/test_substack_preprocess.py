"""
Tests for substack-api-publisher.py preprocessing.

Covers the markdown transforms that run BEFORE Post.from_markdown():
the \\@ citekey-escape strip (2026-08-25) and pandoc attribute
stripping. These protect the published post from renderer-specific
markdown that Substack would otherwise pass through literally.
"""
import json
from pathlib import Path

import pytest

from conftest import load_script

try:
    SAP = load_script('substack-api-publisher.py')
except SystemExit:
    # python-substack is python3.11-only; skip on interpreters without it
    # (the Makefile gate runs the hydra venv 3.14, which has it installed).
    SAP = None

requires_substack = pytest.mark.skipif(
    SAP is None, reason='python-substack not available on this interpreter')


@requires_substack
def test_backslash_at_escape_stripped():
    """The \\@ citekey escape must become a plain @ for Substack."""
    md = 'Follow \\@cremieuxrecueil, \\@perrymetzger on Twitter.'
    out = SAP.preprocess_markdown(md, Path('.'))
    assert '\\@' not in out
    assert '@cremieuxrecueil' in out
    assert '@perrymetzger' in out


@requires_substack
def test_backslash_at_only_strips_escape_not_real_at():
    """Plain @ handles (no backslash) are left untouched."""
    md = 'Follow @cremieuxrecueil and @perrymetzger.'
    out = SAP.preprocess_markdown(md, Path('.'))
    assert out == md


@requires_substack
def test_pandoc_image_attrs_stripped():
    """Pandoc extended attributes are removed before from_markdown."""
    md = '![alt](img/hero.webp){alt="..." width="800" height="450"}'
    out = SAP.preprocess_markdown(md, Path('.'))
    assert '{alt=' not in out
    assert '![alt](' in out


@requires_substack
def test_parse_image_metadata_figures():
    """<figure> alt/caption/tags are extracted into per-image metadata."""
    md = (
        '<figure>\n'
        '<img src="img/hero.webp" alt="A hero" width="400">\n'
        '<figcaption>The hero building.</figcaption>\n'
        '<!-- tags: architecture, green -->\n'
        '</figure>\n'
    )
    meta = SAP.parse_image_metadata(md)
    assert 'img/hero.webp' in meta
    assert meta['img/hero.webp']['alt'] == 'A hero'
    assert meta['img/hero.webp']['caption'] == 'The hero building.'
    assert meta['img/hero.webp']['tags'] == ['architecture', 'green']


@requires_substack
def test_parse_image_metadata_bare_markdown():
    """Bare markdown images get alt from their alt text."""
    meta = SAP.parse_image_metadata('Paragraph ![Caption](img/a.webp) text.')
    assert 'img/a.webp' in meta
    assert meta['img/a.webp']['alt'] == 'Caption'


@requires_substack
def test_attach_image_metadata_sets_alt_and_caption():
    """attach_image_metadata sets alt on image2 and caption on captionedImage."""
    img_meta = {
        'img/hero.webp': {'alt': 'A hero', 'caption': 'Hero caption.', 'tags': ['t1']}
    }
    draft = {
        'draft_body': json.dumps({
            'type': 'doc', 'content': [{
                'type': 'captionedImage',
                'content': [{'type': 'image2', 'attrs': {'src': 'img/hero.webp'}}],
            }],
        })
    }
    SAP.attach_image_metadata(draft, img_meta, {})
    doc = json.loads(draft['draft_body'])
    cap = doc['content'][0]
    assert cap['caption'] == 'Hero caption.'
    img = cap['content'][0]
    assert img['attrs']['alt'] == 'A hero'
    assert img['tags'] == ['t1']


@requires_substack
def test_attach_image_metadata_arweave_reverse_lookup():
    """When draft src is an arweave URL, metadata is found via arweave_urls."""
    abs_path = '/abs/path/img/hero.webp'
    arw = 'https://arweave.net/ABC123'
    img_meta = {abs_path: {'alt': 'A', 'caption': 'Cap.', 'tags': ['x']}}
    arweave_urls = {abs_path: arw}
    draft = {'draft_body': json.dumps({
        'type': 'doc', 'content': [{
            'type': 'captionedImage',
            'content': [{'type': 'image2', 'attrs': {'src': arw}}],
        }],
    })}
    SAP.attach_image_metadata(draft, img_meta, arweave_urls)
    doc = json.loads(draft['draft_body'])
    assert doc['content'][0]['caption'] == 'Cap.'
    img = doc['content'][0]['content'][0]
    assert img['attrs']['alt'] == 'A'
    assert img['tags'] == ['x']
