"""PATH-shadowing regression test.

2026-09-05 review: a stale pre-CDP-era ~/av/bin/fb-publisher.py (7KB) sat
EARLIER in PATH than the canonical bin/hydra/publish/fb-publisher.py
(40KB), so `command -v fb-publisher.py` could resolve the dead one -- and
the publish-post skill documented both as valid. These tests fail whenever
a repo-bin-root script duplicates (as a real file, not a symlink) a
canonical hydra/publish script.
"""
import subprocess
from pathlib import Path

AV_BIN = Path.home() / 'av' / 'bin'
PUB = Path(__file__).resolve().parent.parent  # bin/hydra/publish/

# Scaffolding/test files in the publish dir that are expected to have no
# bin-root counterpart at all (skip them in the duplicate scan).
_SKIP_PREFIXES = ('test_', 'conftest')


def test_no_real_file_at_bin_root_shadows_publish_dir():
    shadows = []
    for p in PUB.iterdir():
        if not p.is_file() or p.name.startswith(_SKIP_PREFIXES):
            continue
        dup = AV_BIN / p.name
        # A symlink to the canonical file is FINE (the intended exposure
        # pattern). A REAL FILE is a diverging copy -> shadow risk.
        if dup.exists() and not dup.is_symlink():
            shadows.append(dup)
    assert not shadows, (
        'Stale real files at ~/av/bin shadow canonical publish scripts '
        '(delete them or replace with symlinks):\n'
        + '\n'.join(str(s) for s in shadows))


def test_path_resolves_canonical_fb_publisher():
    """`command -v fb-publisher.py` must resolve INSIDE hydra/publish."""
    r = subprocess.run(['command -v fb-publisher.py'],
                       shell=True, capture_output=True, text=True)
    if r.stdout.strip():  # assert only when it resolves at all
        assert 'hydra/publish' in r.stdout, (
            f'PATH resolves fb-publisher.py to {r.stdout.strip()} '
            f'-- canonical bin/hydra/publish version is shadowed')
