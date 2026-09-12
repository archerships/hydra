"""Dispatch-argument tests + subprocess-timeout regression guard.

The pub_* dispatch functions (hydra-publish:274-493) build the CLI for
each platform publisher; zero of them had tests (2026-09-05 review), and
the whole orchestrator ran subprocesses without timeouts (CODE.md 3.5
requires timeout=). T3 here pins the exact command construction per
platform; T4 fails if any subprocess call site loses its timeout.
"""
import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / 'hydra-publish'


def load_module():
    loader = importlib.machinery.SourceFileLoader('hydra_publish_dispatch', str(SCRIPT))
    spec = importlib.util.spec_from_loader('hydra_publish_dispatch', loader, origin=str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(SCRIPT)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return load_module()


class _FakeRC:
    returncode = 0


def _fake_run_recorder(calls, rc=None):
    """Return a subprocess.run stand-in that records cmd and returns a fake RC."""
    if rc is None:
        rc = _FakeRC()
    def _run(cmd, **kw):
        calls['cmd'] = cmd
        return rc
    return _run


# ---------------------------------------------------------------------------
# T3: dispatch-argument pinning
# ---------------------------------------------------------------------------

def test_pub_substack_uses_python311_and_cover_flag(mod, monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(mod.subprocess, 'run', _fake_run_recorder(calls))
    monkeypatch.setattr(mod, 'get_dst_html', lambda slug: Path(f'/dst/{slug}.html'))
    monkeypatch.setattr(mod, 'essay_dir', lambda slug: Path(f'/posts/{slug}'))
    # Real temp file: pub_substack only passes --cover if image.exists().
    cover = tmp_path / 'cover.png'
    cover.write_bytes(b'\x89PNG\r\n\x1a\n')
    mod.pub_substack('slug', cover, dry_run=False)
    cmd = calls['cmd']
    assert cmd[0] == '/opt/homebrew/bin/python3.11', 'substack needs 3.11'
    assert cmd[1].endswith('substack-api-publisher.py')
    assert '--essay' in cmd and '--cover' in cmd
    assert str(cover) in cmd


def test_pub_nostr_passes_image_url_and_kind(mod, monkeypatch, tmp_path):
    recorded = {}

    class _NostrRC:
        returncode = 0
        stdout = 'NOSTR_URL=https://yakihonne.com/article/x\n'
        stderr = ''

    def fake_run(cmd, **kw):
        recorded['cmd'] = cmd
        return _NostrRC()

    monkeypatch.setattr(mod.subprocess, 'run', fake_run)
    # essay_to_markdown + extract_intro both parse the built HTML -- give
    # them a minimal real file via the TMP-aware get_dst_html.
    html = tmp_path / 'slug.html'
    html.write_text('<html><body><main><h1>T</h1><p>Intro.</p></main></body></html>')
    monkeypatch.setattr(mod, 'get_dst_html', lambda slug: html)
    monkeypatch.setattr(mod, 'TMP', tmp_path)
    ok, url = mod.pub_nostr('slug', {'title': 'T'},
                            None, 'https://arweave.net/abc',
                            dry_run=False)
    assert ok and url == 'https://yakihonne.com/article/x'
    cmd = recorded['cmd']
    assert '--kind' in cmd and '30023' in cmd
    assert '--image-url' in cmd
    assert 'https://arweave.net/abc' in cmd


def test_pub_twitter_uses_sys_executable(mod, monkeypatch):
    calls = {}
    monkeypatch.setattr(mod.subprocess, 'run', _fake_run_recorder(calls))
    monkeypatch.setattr(mod, 'get_dst_html', lambda slug: Path(f'/dst/{slug}.html'))
    mod.pub_twitter_article('slug', None, dry_run=False)
    assert calls['cmd'][0] == mod.sys.executable
    assert calls['cmd'][1].endswith('x-publisher.py')


# ---------------------------------------------------------------------------
# T4: timeout regression guard (static source scan)
# ---------------------------------------------------------------------------

def test_every_subprocess_call_has_timeout():
    """No bare subprocess.run without timeout= in hydra-publish.

    Before the 2026-09-06 fix, all six call sites ran untimed -- a stuck
    browser or hung launcher would hang the whole publish forever.
    This fails if any site regresses to untimed.
    """
    src = SCRIPT.read_text(encoding='utf-8')
    bad = []
    for m in re.finditer(r'subprocess\.run\(', src):
        seg = src[m.start():m.start() + 700]
        depth = 0
        for i, ch in enumerate(seg):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    seg = seg[:i]
                    break
        if 'timeout=' not in seg:
            bad.append(src[:m.start()].count('\n') + 1)  # 1-based line no
    assert not bad, f'subprocess.run without timeout= at lines: {bad}'
