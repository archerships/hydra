"""CDP-port and deploy-gate regression tests.

2026-09-09 review fixes:
- C1: all site-publish CDP scripts now DEFAULT HYDRA_CDP_URL to the
  dedicated publishing Chromium on :9226 (the build-pipeline convention),
  not the user's personal Brave on :9222. The launch port is derived from
  CDP_URL, so a real deploy no longer silently drives the personal browser.
- Deploy topology: bin/archerships/publish now enforces the SAME `make
  test` gate that scoped-build.sh deploy() runs, so every Pages deploy is
  test-gated regardless of entry point.

These tests fail if the default port regresses to 9222, if the publish
wrapper drops the test gate, or if the launch port is not derived from
CDP_URL.
"""
import re
from pathlib import Path

PUB = Path(__file__).resolve().parent.parent  # bin/hydra/publish/
BIN = Path.home() / 'av' / 'bin'

# Scripts that publish to socials via CDP and MUST default to the dedicated
# publishing Chromium on :9226 (never the personal Brave on :9222).
CDP_SCRIPTS = [
    'x-publisher.py',
    'fb-publisher.py',
    'fb-edit-post.py',
    'fb-delete-post.py',
]

PUBLISH_WRAPPER = BIN / 'archerships' / 'publish'


def _read(p):
    return Path(p).read_text()


def test_cdp_scripts_default_to_9226():
    """All CDP publish scripts must default HYDRA_CDP_URL to :9226."""
    for name in CDP_SCRIPTS:
        src = _read(PUB / name)
        assert 'http://localhost:9226' in src, (
            f'{name} does not default HYDRA_CDP_URL to :9226')


def test_cdp_scripts_do_not_hardcode_9222():
    """No hardcoded 9222 left in the connect/launch logic."""
    for name in CDP_SCRIPTS:
        src = _read(PUB / name)
        # The only allowed mention is none at all.
        assert '9222' not in src, (
            f'{name} still references :9222 -- must use the :9226 default '
            f'or derive the port from CDP_URL')


def test_x_publisher_derives_launch_port_from_cdp_url():
    """x-publisher must derive the Chromium launch port from CDP_URL, so it
    launches on the SAME port it will connect to (9226 by default)."""
    src = _read(PUB / 'x-publisher.py')
    assert 'CDP_PORT' in src, 'x-publisher.py lost the CDP_PORT variable'
    assert 'f"--remote-debugging-port={CDP_PORT}"' in src, (
        'x-publisher.py launch no longer uses CDP_PORT')


def test_publish_wrapper_runs_make_test_gate():
    """bin/archerships/publish must run the full `make test` suite before
    the Pages deploy (same gate as scoped-build deploy())."""
    src = _read(PUBLISH_WRAPPER)
    assert 'make test' in src, (
        'publish wrapper no longer runs the make test gate')
    assert 'tests FAILED -- deploy aborted' in src, (
        'publish wrapper no longer aborts on test failure')
    assert 'wrangler pages deploy' in src, 'publish wrapper lost the deploy'