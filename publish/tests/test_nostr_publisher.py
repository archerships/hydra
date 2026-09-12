"""
Tests for publish/src/nostr-publisher.py

All tests use --dry-run so no real relay connections are made.
The nostr_test_key fixture (conftest.py) temporarily stores a test key
in the system keychain for the duration of each test.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / 'nostr-publisher.py'
TEST_CONTENT = 'Test announcement from hydra test suite.'


def run(*args, **kw):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True, text=True, **kw
    )


def parse_event_json(stdout: str) -> dict:
    """Extract and parse the JSON block printed by --dry-run."""
    match = re.search(r'\{.*\}', stdout, re.DOTALL)
    assert match, f'No JSON found in output:\n{stdout}'
    return json.loads(match.group())


# ---------------------------------------------------------------------------
# Kind 1 -- short note
# ---------------------------------------------------------------------------

def test_kind1_dry_run_succeeds(nostr_test_key):
    r = run('--kind', '1', '--content', TEST_CONTENT, '--dry-run')
    assert r.returncode == 0, r.stderr


def test_kind1_event_has_correct_kind(nostr_test_key):
    r = run('--kind', '1', '--content', TEST_CONTENT, '--dry-run')
    event = parse_event_json(r.stdout)
    assert event['kind'] == 1


def test_kind1_event_contains_content(nostr_test_key):
    r = run('--kind', '1', '--content', TEST_CONTENT, '--dry-run')
    event = parse_event_json(r.stdout)
    assert TEST_CONTENT in event['content']


def test_kind1_url_appended_to_content(nostr_test_key):
    url = 'https://archerships.com/essays/test.html'
    r = run('--kind', '1', '--content', TEST_CONTENT, '--url', url, '--dry-run')
    event = parse_event_json(r.stdout)
    assert url in event['content']


def test_kind1_image_tag_present(nostr_test_key):
    img_url = 'https://example.com/cover.jpg'
    r = run('--kind', '1', '--content', TEST_CONTENT,
            '--image-url', img_url, '--dry-run')
    event = parse_event_json(r.stdout)
    image_tags = [t for t in event['tags'] if t[0] == 'image']
    assert image_tags, 'No image tag found'
    assert image_tags[0][1] == img_url


def test_kind1_hashtags_present(nostr_test_key):
    r = run('--kind', '1', '--content', TEST_CONTENT,
            '--tags', 'privacy,bitcoin', '--dry-run')
    event = parse_event_json(r.stdout)
    t_tags = [t[1] for t in event['tags'] if t[0] == 't']
    assert 'privacy' in t_tags
    assert 'bitcoin' in t_tags


def test_kind1_content_from_file(nostr_test_key, tmp_path):
    f = tmp_path / 'note.txt'
    f.write_text('Content from file.', encoding='utf-8')
    r = run('--kind', '1', '--file', str(f), '--dry-run')
    assert r.returncode == 0, r.stderr
    event = parse_event_json(r.stdout)
    assert 'Content from file.' in event['content']


def test_kind1_event_has_id_and_sig(nostr_test_key):
    r = run('--kind', '1', '--content', TEST_CONTENT, '--dry-run')
    event = parse_event_json(r.stdout)
    assert event.get('id'), 'Missing id'
    assert event.get('sig'), 'Missing sig'


def test_kind1_event_has_pubkey(nostr_test_key):
    r = run('--kind', '1', '--content', TEST_CONTENT, '--dry-run')
    event = parse_event_json(r.stdout)
    assert re.fullmatch(r'[0-9a-f]{64}', event.get('pubkey', '')), \
        f'Invalid pubkey: {event.get("pubkey")}'


# ---------------------------------------------------------------------------
# Kind 30023 -- long-form article
# ---------------------------------------------------------------------------

def test_kind30023_dry_run_succeeds(nostr_test_key):
    r = run('--kind', '30023',
            '--content', '# Article\n\nBody text.',
            '--title', 'My Article',
            '--slug', 'my-article',
            '--dry-run')
    assert r.returncode == 0, r.stderr


def test_kind30023_event_has_correct_kind(nostr_test_key):
    r = run('--kind', '30023',
            '--content', 'Body.',
            '--title', 'Title',
            '--slug', 'my-slug',
            '--dry-run')
    event = parse_event_json(r.stdout)
    assert event['kind'] == 30023


def test_kind30023_has_d_tag(nostr_test_key):
    r = run('--kind', '30023',
            '--content', 'Body.',
            '--title', 'Title',
            '--slug', 'unique-slug',
            '--dry-run')
    event = parse_event_json(r.stdout)
    d_tags = [t[1] for t in event['tags'] if t[0] == 'd']
    assert d_tags == ['unique-slug']


def test_kind30023_has_title_tag(nostr_test_key):
    r = run('--kind', '30023',
            '--content', 'Body.',
            '--title', 'My Great Article',
            '--slug', 'great',
            '--dry-run')
    event = parse_event_json(r.stdout)
    title_tags = [t[1] for t in event['tags'] if t[0] == 'title']
    assert title_tags == ['My Great Article']


def test_kind30023_has_image_tag(nostr_test_key):
    img = 'https://example.com/cover.jpg'
    r = run('--kind', '30023',
            '--content', 'Body.',
            '--title', 'T',
            '--slug', 's',
            '--image-url', img,
            '--dry-run')
    event = parse_event_json(r.stdout)
    assert any(t[0] == 'image' and t[1] == img for t in event['tags'])


def test_kind30023_has_published_at_tag(nostr_test_key):
    r = run('--kind', '30023',
            '--content', 'Body.',
            '--title', 'T',
            '--slug', 's',
            '--dry-run')
    event = parse_event_json(r.stdout)
    pa_tags = [t for t in event['tags'] if t[0] == 'published_at']
    assert pa_tags, 'No published_at tag'
    assert pa_tags[0][1].isdigit()


def test_kind30023_url_becomes_r_tag(nostr_test_key):
    url = 'https://archerships.com/essays/test.html'
    r = run('--kind', '30023',
            '--content', 'Body.',
            '--title', 'T',
            '--slug', 's',
            '--url', url,
            '--dry-run')
    event = parse_event_json(r.stdout)
    r_tags = [t[1] for t in event['tags'] if t[0] == 'r']
    assert url in r_tags


def test_kind30023_requires_title(nostr_test_key):
    r = run('--kind', '30023', '--content', 'Body.', '--slug', 's', '--dry-run')
    assert r.returncode != 0


def test_kind30023_requires_slug(nostr_test_key):
    r = run('--kind', '30023', '--content', 'Body.', '--title', 'T', '--dry-run')
    assert r.returncode != 0


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_no_content_or_file_exits(nostr_test_key):
    r = run('--kind', '1', '--dry-run')
    assert r.returncode != 0


def test_missing_keychain_key_exits():
    """Without the nostr_test_key fixture, no key is set — script must exit."""
    import keyring
    original = keyring.get_password('nostr', 'private_key')
    if original:
        pytest.skip('Real nostr key is set; cannot test missing-key path safely.')
    r = run('--kind', '1', '--content', 'test', '--dry-run')
    assert r.returncode != 0
