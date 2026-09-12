"""
Shared fixtures for Hydra publish test suite.
"""
import importlib.util
import sys
from pathlib import Path

import keyring
import pytest

BIN = Path(__file__).parent.parent  # publish/src/

# A deterministic 32-byte test private key (not used on any real relay).
TEST_NOSTR_KEY_HEX = 'deadbeef' * 8


def load_script(filename: str):
    """Load a publish/src/ script (with or without .py extension) as a module."""
    path = BIN / filename
    mod_name = filename.replace('-', '_').replace('.py', '')
    # Ensure sibling imports (e.g. signal_arweave) resolve when exec'ing a
    # script that is not on sys.path.
    if str(BIN) not in sys.path:
        sys.path.insert(0, str(BIN))
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def nostr_test_key():
    """
    Temporarily store a test private key in the system keychain so that
    nostr-publisher.py subprocess calls can authenticate without a real key.
    Restores the original value (or deletes it) on teardown.
    """
    original = keyring.get_password('nostr', 'private_key')
    keyring.set_password('nostr', 'private_key', TEST_NOSTR_KEY_HEX)
    yield TEST_NOSTR_KEY_HEX
    if original:
        keyring.set_password('nostr', 'private_key', original)
    else:
        try:
            keyring.delete_password('nostr', 'private_key')
        except keyring.errors.PasswordDeleteError:
            pass


@pytest.fixture()
def essay_dir(tmp_path):
    """
    Create a minimal essay directory structure in tmp_path, mirroring
    src/essays/ and dst/essays/ so hydra-publish functions can run.
    """
    content = tmp_path / 'src' / 'essays'
    dst     = tmp_path / 'dst' / 'essays'
    content.mkdir(parents=True)
    dst.mkdir(parents=True)
    return {'content': content, 'dst': dst, 'root': tmp_path}


def make_essay_md(content_dir: Path, slug: str, extra_fm: str = '') -> Path:
    """Write a minimal essay .md file and return its path."""
    path = content_dir / f'{slug}.md'
    path.write_text(
        f'---\ntype: essay\ntitle: "Test Essay"\ndate: 2026-01-01\n'
        f'tags: ["test","hydra"]\npublished_at: []\n{extra_fm}---\n',
        encoding='utf-8',
    )
    return path


def make_essay_html(dst_dir: Path, slug: str, title: str = 'Test Essay',
                    intro: str = 'Intro paragraph text.') -> Path:
    """Write a minimal built essay HTML file and return its path."""
    path = dst_dir / f'{slug}.html'
    path.write_text(
        f'<!DOCTYPE html><html><body><main>'
        f'<h1>{title}</h1><p>{intro}</p>'
        f'</main></body></html>',
        encoding='utf-8',
    )
    return path
