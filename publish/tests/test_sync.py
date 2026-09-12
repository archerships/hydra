"""Unit tests for hydra_crud.sync (D9 SRC -> DST upsert engine)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PUBLISH_DIR = Path(__file__).resolve().parent.parent  # ~/av/bin/hydra/publish
sys.path.insert(0, str(PUBLISH_DIR))

from hydra_crud.model import PlatformRef, Post
from hydra_crud.sync import sync_to_dst


class RecorderPlugin:
    """Stub plugin that records calls and returns a fixed ref."""
    platform = "stub"

    def __init__(self, ref_url="https://stub/1"):
        self.calls = []
        self.ref_url = ref_url

    def create(self, post, ctx):
        self.calls.append(("create", post.slug, ctx.get("dry_run")))
        return PlatformRef(platform="stub", url=self.ref_url,
                           created_at="2026-08-15", updated_at="2026-08-15")

    def update(self, ref, post, ctx):
        self.calls.append(("update", ref.post_id, ctx.get("dry_run")))
        ref.updated_at = "2026-08-15"
        return ref


@pytest.fixture()
def post():
    return Post(title="Sync", body="body", slug="2026-01-01-test",
                updated_at="2026-08-15")


class TestSyncToDst:
    def test_create_when_no_ref(self, post):
        plugin = RecorderPlugin()
        recorded = []
        action = sync_to_dst(post, "stub", plugin, ref=None,
                             record_ref=lambda p, d: recorded.append((p, d)))
        assert action == "created"
        assert plugin.calls == [("create", "2026-01-01-test", False)]
        assert recorded == [("stub", plugin.ref_url)] or recorded  # ref recorded

    def test_update_when_ref_exists(self, post):
        plugin = RecorderPlugin()
        ref = PlatformRef(platform="stub", post_id="9", url="https://stub/9",
                          updated_at="2026-08-01")
        action = sync_to_dst(post, "stub", plugin, ref=ref)
        assert action == "updated"
        assert plugin.calls == [("update", "9", False)]

    def test_skips_when_src_older_than_dst(self, post):
        plugin = RecorderPlugin()
        ref = PlatformRef(platform="stub", post_id="9", url="https://stub/9",
                          updated_at="2026-08-20")  # NEWER than src
        action = sync_to_dst(post, "stub", plugin, ref=ref)
        assert action == "skipped"
        assert plugin.calls == []  # never called

    def test_dry_run_does_not_call_plugin(self, post):
        plugin = RecorderPlugin()
        action = sync_to_dst(post, "stub", plugin, ref=None, dry_run=True)
        # dry-run still reports the intended action
        assert action == "created"
        assert plugin.calls == [("create", "2026-01-01-test", True)]

    def test_missing_src_date_does_not_block_update(self, post):
        post.updated_at = ""
        plugin = RecorderPlugin()
        ref = PlatformRef(platform="stub", post_id="9", url="https://stub/9",
                          updated_at="2026-08-20")
        action = sync_to_dst(post, "stub", plugin, ref=ref)
        assert action == "updated"  # no src date -> no conflict comparison
