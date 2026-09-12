"""Launcher integrity: every script hydra-publish invokes must exist,
be executable, and not be a dangling symlink.

Written after the 2026-09-05 review found BIN/'substack-post-url' dangling
(old prj/hydra/publish/src target, silently skipped by an .exists() guard,
so every real Substack publish skipped URL back-fill). These tests fail on
ANY dead launcher so that class of bug cannot ship silently again.
"""
import re
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent  # bin/hydra/publish/

# Every launcher hydra-publish dispatches to (from its BIN call sites:
# _run paths in pub_* functions + the substack URL back-fill).
LAUNCHERS = [
    'hydra-adapt-image',
    'x-publisher.py',
    'fb-publisher.py',
    'substack-api-publisher.py',
    'nostr-publisher.py',
    'substack-post-url.py',
    'hydra-signal',
]


def test_launchers_exist_executable_not_dangling():
    missing = []
    for name in LAUNCHERS:
        p = BIN / name
        if not p.exists():
            target = f' (symlink -> {p.readlink()})' if p.is_symlink() else ''
            missing.append(f'{name}: missing or dangling{target}')
        elif not (p.stat().st_mode & 0o111):
            missing.append(f'{name}: not executable')
    assert not missing, 'Broken launchers:\n' + '\n'.join(missing)


def test_hydra_publish_references_resolve():
    """Every BIN / '<name>' literal in hydra-publish must resolve.

    The substack URL back-fill referenced 'substack-post-url' (a dangling
    symlink to the old prj/hydra/publish/src layout) from 2026-06 until
    2026-09-06 -- caught only by manual review. This extracts the literals
    so the dead-launcher class is a test failure, not a silent skip.
    """
    src = (BIN / 'hydra-publish').read_text(encoding='utf-8')
    referenced = set(re.findall(r"BIN / '([A-Za-z0-9._-]+)'", src))
    # Only names that look like launchers (have an extension or are known
    # script names) -- BIN is also used for output paths in theory.
    assert referenced, 'no BIN literals found -- extraction pattern broke'
    broken = [name for name in sorted(referenced)
              if not (BIN / name).exists()]
    assert not broken, f'hydra-publish references dead launchers: {broken}'
