"""
Shared fixtures for Hydra ingest test suite.
"""
import importlib.util
import sys
from pathlib import Path

import keyring
import pytest

BIN = Path(__file__).parent.parent  # ingest/src/

# A deterministic 32-byte test private key (not used on any real relay).
TEST_NOSTR_KEY_HEX = 'deadbeef' * 8


def load_script(filename: str):
    """Load an ingest/src/ script (with or without .py extension) as a module."""
    path = BIN / filename
    mod_name = filename.replace('-', '_').replace('.py', '')
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def nostr_test_key():
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
