"""Unit tests for hydra_crud model + registry + frontmatter refs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PUBLISH_DIR = Path(__file__).resolve().parent.parent  # ~/av/bin/hydra/publish
sys.path.insert(0, str(PUBLISH_DIR))

from hydra_crud.model import PlatformRef, Post, PostStatus
from hydra_crud.registry import RegistryError, get, known_platforms


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

class TestPlatformRef:
    def test_roundtrip(self):
        ref = PlatformRef(platform="facebook", account="archerships",
                          destination="profile", post_id="123",
                          url="https://www.facebook.com/x/1",
                          post_type="article")
        assert PlatformRef.from_dict(ref.to_dict()) == ref

    def test_from_dict_ignores_unknown_keys(self):
        ref = PlatformRef.from_dict({"platform": "x", "bogus": 1, "url": "u"})
        assert ref.platform == "x"
        assert ref.url == "u"
        assert not hasattr(ref, "bogus")

    def test_today_is_iso(self):
        import datetime
        assert PlatformRef.today() == datetime.date.today().isoformat()


class TestPost:
    def test_defaults(self):
        p = Post(title="t", body="b")
        assert p.post_type == "article"
        assert p.tags == []
        assert p.url == ""

    def test_to_dict(self):
        p = Post(title="t", slug="s", post_type="image")
        d = p.to_dict()
        assert d["post_type"] == "image"
        assert d["slug"] == "s"


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_facebook_plugin_available(self):
        p = get("facebook")
        assert p.platform == "facebook"
        assert p.transport == "cdp"
        assert p.anonymous is False
        assert {"article", "image", "tweet"} <= p.post_types

    def test_unknown_platform_raises(self):
        with pytest.raises(RegistryError):
            get("definitely-not-a-platform")

    def test_known_platforms_contains_facebook(self):
        assert "facebook" in known_platforms()


# ---------------------------------------------------------------------------
# frontmatter refs
# ---------------------------------------------------------------------------

class TestFrontmatterRefs:
    @pytest.fixture()
    def essay(self, tmp_path):
        p = tmp_path / "2026-01-01-test.md"
        p.write_text(
            "---\n"
            "type: long-form\n"
            "title: \"Test\"\n"
            "published_at:\n"
            "- platform: twitter\n"
            "  url: https://x.com/archerships/1\n"
            "- platform: substack\n"
            "  url: https://archerships.substack.com/p/1\n"
            "---\n\n"
            "Body.\n",
            encoding="utf-8",
        )
        return p

    def test_get_ref(self, essay):
        from essay_frontmatter import get_platform_ref
        ref = get_platform_ref(essay, "twitter")
        assert ref is not None
        assert ref["url"] == "https://x.com/archerships/1"

    def test_get_ref_missing(self, essay):
        from essay_frontmatter import get_platform_ref
        assert get_platform_ref(essay, "facebook") is None

    def test_set_ref_adds(self, essay):
        from essay_frontmatter import get_platform_ref, set_platform_ref
        set_platform_ref(essay, "facebook",
                         {"id": "456", "url": "https://www.facebook.com/456",
                          "post_type": "article", "destination": "profile"})
        ref = get_platform_ref(essay, "facebook")
        assert ref["id"] == "456"
        assert ref["post_type"] == "article"
        # other entries preserved
        assert get_platform_ref(essay, "twitter")["url"] == "https://x.com/archerships/1"

    def test_set_ref_updates(self, essay):
        from essay_frontmatter import get_platform_ref, set_platform_ref
        set_platform_ref(essay, "twitter", {"id": "999"})
        ref = get_platform_ref(essay, "twitter")
        assert ref["id"] == "999"
        assert ref["url"] == "https://x.com/archerships/1"  # preserved

    def test_remove_entry(self, essay):
        from essay_frontmatter import get_platform_ref, remove_platform_entry
        assert remove_platform_entry(essay, "twitter") is True
        assert get_platform_ref(essay, "twitter") is None
        assert get_platform_ref(essay, "substack") is not None

    def test_remove_missing_entry_returns_false(self, essay):
        from essay_frontmatter import remove_platform_entry
        assert remove_platform_entry(essay, "facebook") is False


class TestPostStatus:
    def test_defaults(self):
        s = PostStatus(exists=False)
        assert s.exists is False
        assert s.url == ""
