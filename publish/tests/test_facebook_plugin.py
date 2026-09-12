"""Unit tests for the facebook CRUD plugin (subprocess mocked)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PUBLISH_DIR = Path(__file__).resolve().parent.parent  # ~/av/bin/hydra/publish
sys.path.insert(0, str(PUBLISH_DIR))

from hydra_crud.model import PlatformRef, Post
from hydra_crud.registry import get


@pytest.fixture()
def plugin():
    return get("facebook")


@pytest.fixture()
def post(tmp_path):
    return Post(title="Test Post", body="Hello world https://archerships.com/x",
                slug="2026-01-01-test", url="https://archerships.com/essays/2026-01-01-test.html",
                post_type="article")


@pytest.fixture()
def ctx(tmp_path):
    return {
        "tmp_dir": str(tmp_path / "tmp"),
        "essay_dir": str(tmp_path / "essay"),
        "contact_file": str(tmp_path / "fb-contact.txt"),
        "dry_run": False,
        "account": "archerships",
        "destination": "profile",
    }


class TestFacebookCreate:
    def test_builds_body_file_with_urls_stripped(self, plugin, post, ctx):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            plugin.create(post, ctx)
        # find the body file path passed to fb-publisher.py
        call = run.call_args[0][0]
        body_file = Path(call[2])
        text = body_file.read_text(encoding="utf-8")
        assert "https://archerships.com" not in text
        assert "Hello world" in text

    def test_strips_markdown_image_and_emphasis(self, plugin, ctx):
        post = Post(title="Img", slug="2026-01-01-img",
                    body="![alt](img/hero.png)\n\n**Bold** text _em_",
                    url="https://archerships.com/essays/x.html")
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            plugin.create(post, ctx)
        call = run.call_args[0][0]
        text = Path(call[2]).read_text(encoding="utf-8")
        assert "![" not in text
        assert "**Bold**" not in text
        assert "Bold text em" in text

    def test_passes_essay_url_and_dir(self, plugin, post, ctx):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            plugin.create(post, ctx)
        call = run.call_args[0][0]
        joined = " ".join(str(x) for x in call)
        assert "--essay-url" in joined
        assert "https://archerships.com/essays/2026-01-01-test.html" in joined
        assert "--essay-dir" in joined

    def test_dry_run_does_not_execute(self, plugin, post, ctx):
        ctx["dry_run"] = True
        with patch("subprocess.run") as run:
            plugin.create(post, ctx)
        run.assert_not_called()

    def test_returns_platform_ref(self, plugin, post, ctx):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            ref = plugin.create(post, ctx)
        assert ref.platform == "facebook"
        assert ref.account == "archerships"
        assert ref.destination == "profile"
        assert ref.post_type == "article"


class TestFacebookUpdate:
    def test_calls_fb_edit_with_url_and_body(self, plugin, post, ctx):
        ref = PlatformRef(platform="facebook", url="https://www.facebook.com/123")
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            plugin.update(ref, post, ctx)
        call = run.call_args[0][0]
        joined = " ".join(str(x) for x in call)
        assert "fb-edit-post.py" in joined
        assert "--url" in joined
        assert "https://www.facebook.com/123" in joined
        assert "--body-file" in joined

    def test_updates_timestamp(self, plugin, post, ctx):
        ref = PlatformRef(platform="facebook", url="https://www.facebook.com/123")
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            out = plugin.update(ref, post, ctx)
        assert out.updated_at == PlatformRef.today()


class TestFacebookDelete:
    def test_calls_fb_delete_with_url(self, plugin, post, ctx):
        ref = PlatformRef(platform="facebook", url="https://www.facebook.com/123")
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            plugin.delete(ref, ctx)
        call = run.call_args[0][0]
        joined = " ".join(str(x) for x in call)
        assert "fb-delete-post.py" in joined
        assert "--url" in joined
        assert "https://www.facebook.com/123" in joined

    def test_delete_with_post_id(self, plugin, post, ctx):
        ref = PlatformRef(platform="facebook", post_id="987")
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            plugin.delete(ref, ctx)
        joined = " ".join(str(x) for x in run.call_args[0][0])
        assert "--post-id" in joined
        assert "987" in joined

    def test_delete_without_ref_raises(self, plugin, post, ctx):
        ref = PlatformRef(platform="facebook")
        with pytest.raises(Exception):
            plugin.delete(ref, ctx)

    def test_yes_flag_passthrough(self, plugin, post, ctx):
        ctx["yes"] = True
        ref = PlatformRef(platform="facebook", url="https://www.facebook.com/123")
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            plugin.delete(ref, ctx)
        joined = " ".join(str(x) for x in run.call_args[0][0])
        assert "--yes" in joined
