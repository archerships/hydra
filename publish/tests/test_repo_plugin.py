"""Unit tests for the repo plugin (filesystem source-of-truth CRUD)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PUBLISH_DIR = Path(__file__).resolve().parent.parent  # ~/av/bin/hydra/publish
sys.path.insert(0, str(PUBLISH_DIR))

from hydra_crud.model import PlatformRef, Post
from hydra_crud.registry import get


@pytest.fixture()
def plugin():
    return get("repo")


@pytest.fixture()
def ctx(tmp_path):
    return {"posts_dir": str(tmp_path / "posts"), "archive_dir": str(tmp_path / "archive")}


class TestRepoCreate:
    def test_creates_md_with_frontmatter(self, plugin, ctx):
        post = Post(title="Hello World", body="Body text", slug="",
                    post_type="article", tags=["tech"])
        ref = plugin.create(post, ctx)
        d = Path(ctx["posts_dir"]) / ref.post_id
        md = d / f"{ref.post_id}.md"
        assert md.exists()
        text = md.read_text(encoding="utf-8")
        assert "title: Hello World" in text
        assert "Body text" in text
        assert "- tech" in text

    def test_slug_generated_from_title_with_date(self, plugin, ctx):
        post = Post(title="Hello World", body="b")
        ref = plugin.create(post, ctx)
        assert ref.post_id.startswith("2026-")
        assert ref.post_id.endswith("-hello-world")

    def test_existing_dir_raises(self, plugin, ctx, tmp_path):
        post = Post(title="Dup", body="b")
        plugin.create(post, ctx)
        with pytest.raises(Exception):
            plugin.create(post, ctx)  # same generated slug


class TestRepoRead:
    def test_read_existing(self, plugin, ctx):
        post = Post(title="Read Me", body="b")
        ref = plugin.create(post, ctx)
        status = plugin.read(ref, ctx)
        assert status.exists is True
        assert ref.post_id in status.url

    def test_read_missing(self, plugin, ctx):
        ref = PlatformRef(platform="repo", post_id="nonexistent")
        status = plugin.read(ref, ctx)
        assert status.exists is False


class TestRepoFetch:
    def test_fetch_builds_post_from_files(self, plugin, ctx):
        post = Post(title="Fetch Me", body="body text", tags=["a"], post_type="article")
        ref = plugin.create(post, ctx)
        fetched = plugin.fetch_post(ref, ctx)
        assert fetched.title == "Fetch Me"
        assert "body text" in fetched.body
        assert fetched.slug == ref.post_id
        assert fetched.post_type == "article"


class TestRepoUpdate:
    def test_update_rewrites_body_and_title(self, plugin, ctx):
        post = Post(title="Orig", body="original")
        ref = plugin.create(post, ctx)
        new_post = Post(title="Changed", body="new body", tags=["x"])
        plugin.update(ref, new_post, ctx)
        md = Path(ctx["posts_dir"]) / ref.post_id / f"{ref.post_id}.md"
        text = md.read_text(encoding="utf-8")
        assert "title: Changed" in text
        assert "new body" in text
        assert "original" not in text


class TestRepoDelete:
    def test_delete_archives_not_removes(self, plugin, ctx):
        post = Post(title="Archive Me", body="b")
        ref = plugin.create(post, ctx)
        plugin.delete(ref, ctx)
        src = Path(ctx["posts_dir"]) / ref.post_id
        assert not src.exists()          # moved out of posts
        archive = Path(ctx["archive_dir"])
        assert archive.exists()
        assert any(archive.iterdir())    # something archived

    def test_delete_missing_raises(self, plugin, ctx):
        ref = PlatformRef(platform="repo", post_id="nope")
        with pytest.raises(Exception):
            plugin.delete(ref, ctx)


class TestRepoList:
    def test_list_returns_posts(self, plugin, ctx):
        plugin.create(Post(title="One", body="b"), ctx)
        plugin.create(Post(title="Two", body="b"), ctx)
        refs = plugin.list(ctx)
        assert len(refs) == 2
        assert all(r.platform == "repo" for r in refs)
