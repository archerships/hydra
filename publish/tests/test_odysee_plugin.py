"""Unit tests for the Odysee CRUD plugin (transport mocked; NO network).

Covers the plugin contract: registration, keyring auth, dry-run behavior,
the v4 publish pipeline invocation (upload token -> TUS -> stream_create
-> poll), claim-param construction, resolve read, same-name update, and
abandon delete. All HTTP goes through the plugin's _http helper, which is
monkeypatched -- these tests never touch api.na-backend.odysee.com.

The v4 pipeline shape (reconstructed from OdyseeTeam/odysee-frontend
web/setup/publish-v4-tasks.ts): uploads/ token -> TUS at location with
Bearer -> JSON-RPC stream_create with file_path=upload URL -> poll
{ASYN}/{query_id} (204 pending, 200 done).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PUBLISH_DIR = Path(__file__).resolve().parent.parent  # ~/av/bin/hydra/publish
sys.path.insert(0, str(PUBLISH_DIR))

from hydra_crud.model import Post, PlatformRef
from hydra_crud.registry import get, known_platforms


@pytest.fixture()
def plugin():
    return get("odysee")


@pytest.fixture()
def post():
    return Post(title="Her Love Is A Little Radioactive",
                body="A short about radioactive romance.",
                slug="2026-09-05-her-love-is-a-little-radioactive",
                url="https://archerships.com/essays/her-love.html",
                tags=["Shorts", "Animation"],
                post_type="video")


@pytest.fixture()
def ctx(tmp_path):
    return {
        "tmp_dir": str(tmp_path / "tmp"),
        "essay_dir": str(tmp_path / "essay"),
        "dry_run": False,
        "account": "archerships",
        "destination": "channel",
    }


@pytest.fixture()
def fake_keyring(monkeypatch):
    """Keyring entries for odysee auth + channel id."""
    import keyring as real_keyring
    store = {"odysee/auth_token": "TESTTOKEN123",
             "odysee/channel_id": "abc123channel"}
    def get_pw(service, key):
        return store.get(f"{service}/{key}")
    monkeypatch.setattr(real_keyring, "get_password", get_pw)
    return store


def _fake_http_handler(statuses):
    """Build an _http replacement driven by (method, url) matching.

    statuses: list of (match_fn, (status, headers, body)) consulted in order.
    """
    def fake_http(method, url, headers=None, data=None, timeout=120):
        for match, resp in statuses:
            if match(method, url):
                return resp
        raise AssertionError(f"unexpected _http call: {method} {url}")
    return fake_http


class TestRegistration:
    def test_platform_registered(self):
        assert "odysee" in known_platforms()

    def test_plugin_attributes(self, plugin):
        assert plugin.platform == "odysee"
        assert plugin.transport == "api"
        assert plugin.anonymous is False
        assert "video" in plugin.post_types
        assert plugin.destinations == {"channel"}


class TestAuth:
    def test_missing_token_raises_clear_error(self, plugin, monkeypatch):
        import keyring as real_keyring
        monkeypatch.setattr(real_keyring, "get_password",
                            lambda s, k: None)
        with pytest.raises(Exception, match="auth token not found"):
            plugin.create(post=Post(title="x", slug="x"), ctx={})

    def test_token_loaded_from_keyring(self, plugin, fake_keyring):
        from hydra_crud.plugins.odysee import _auth_token
        assert _auth_token() == "TESTTOKEN123"

    def test_channel_id_from_keyring(self, plugin, fake_keyring):
        from hydra_crud.plugins.odysee import _channel_id
        assert _channel_id({}) == "abc123channel"

    def test_channel_id_falls_back_to_ctx(self, plugin, monkeypatch):
        import keyring as real_keyring
        monkeypatch.setattr(real_keyring, "get_password",
                            lambda s, k: None)
        from hydra_crud.plugins.odysee import _channel_id
        assert _channel_id({"odysee_channel_id": "xyz"}) == "xyz"


class TestCreate:
    def test_dry_run_never_touches_network(self, plugin, post, ctx, fake_keyring):
        ctx["dry_run"] = True
        with patch("hydra_crud.plugins.odysee._http") as http:
            ref = plugin.create(post, ctx)
            http.assert_not_called()
        assert ref.platform == "odysee"
        assert ref.destination == "channel"
        assert "odysee.com/" in ref.url

    def test_video_post_without_file_raises(self, plugin, post, ctx, fake_keyring):
        # essay_dir does not exist -> no video found
        with pytest.raises(Exception, match="no video file found"):
            plugin.create(post, ctx)

    def test_video_post_runs_v4_pipeline(self, plugin, post, ctx, fake_keyring,
                                         tmp_path, monkeypatch):
        essay = tmp_path / "essay"
        essay.mkdir()
        (essay / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        ctx["essay_dir"] = str(essay)

        # Freeze poll waits so the test is fast.
        monkeypatch.setattr("hydra_crud.plugins.odysee.time.sleep", lambda s: None)

        seen = []
        def fake_http(method, url, headers=None, data=None, timeout=120):
            seen.append((method, url))
            if method == "POST" and url.endswith("/uploads/"):
                return (200, {}, json.dumps({
                    "status": "upload_token_created",
                    "payload": {"token": "BEARER1",
                                "location": "https://up/v4/tus-loc"}}).encode())
            if method == "POST" and "tus-loc" in url:
                return (201, {"Location": "https://up/v4/tus-loc/abc"}, b"")
            if method == "PATCH":
                return (204, {"Upload-Offset": str(len(data or b""))}, b"")
            if method == "POST" and url.endswith("/asynqueries/"):
                return (200, {}, json.dumps({
                    "status": "query_created",
                    "payload": {"query_id": 42}}).encode())
            if method == "GET" and url.endswith("/42"):
                return (200, {}, json.dumps({
                    "result": {"outputs": [
                        {"claim_id": "feedcafe1",
                         "permanent_url": "lbry://her-love-video"}]}}).encode())
            raise AssertionError(f"unexpected {method} {url}")

        with patch("hydra_crud.plugins.odysee._http", fake_http):
            ref = plugin.create(post, ctx)

        # Pipeline order: token -> tus create -> patch -> stream_create -> poll
        assert seen[0][1].endswith("/uploads/")
        assert any(m == "POST" and "tus-loc" in u for m, u in seen[1:])
        assert any(m == "PATCH" for m, _ in seen)
        assert seen[-2][1].endswith("/asynqueries/")
        assert seen[-1][1].endswith("/42")
        assert ref.post_id == "feedcafe1"
        assert ref.url == "https://odysee.com/her-love-video"

    def test_stream_create_payload_carries_file_url(self, plugin, fake_keyring,
                                                    tmp_path):
        essay = tmp_path / "essay"
        essay.mkdir()
        (essay / "video.mp4").write_bytes(b"ftyp")
        rpc = {}
        def fake_stream_create(token, file_url, params):
            rpc["file_url"] = file_url
            rpc["params"] = params
            return 7
        with patch.object(plugin, "_request_upload_token",
                          return_value=("B", "https://up/loc")), \
             patch.object(plugin, "_tus_upload",
                          return_value="https://up/loc/xyz"), \
             patch.object(plugin, "_stream_create", fake_stream_create), \
             patch.object(plugin, "_poll_query",
                          return_value={"result": {"outputs": [
                              {"claim_id": "c1",
                               "permanent_url": "lbry://n1"}]}}):
            ref = plugin.create(
                Post(title="T", body="b", slug="t-1", post_type="video",
                     tags=["x"]),
                {"essay_dir": str(essay), "dry_run": False})
        # stream_create must receive the TUS upload URL as file_path
        assert rpc["file_url"] == "https://up/loc/xyz"
        assert "file_path" not in rpc["params"]  # passed via arg, not duplicated
        assert ref.post_id == "c1"

    def test_claim_params_shape(self, plugin, post, ctx, fake_keyring):
        params = plugin._claim_params(post, ctx, None)
        assert params["name"] == "2026-09-05-her-love-is-a-little-radioactive"
        assert params["title"] == post.title
        assert params["bid"] == "0.001"
        assert params["channel_id"] == "abc123channel"
        assert params["tags"] == ["shorts", "animation"]
        assert len(params["name"]) <= 60

    def test_claim_name_sanitized(self, plugin, ctx, fake_keyring):
        weird = Post(title="T", slug="2026_Weird Slug!!", post_type="article")
        params = plugin._claim_params(weird, ctx, None)
        assert params["name"] == "2026weirdslug"  # underscores/spaces/bangs dropped

    def test_no_claim_id_in_response_raises(self, plugin, ctx, fake_keyring):
        def fake_http(method, url, headers=None, data=None, timeout=120):
            # article-type -> proxy publish with empty outputs
            return (200, {}, json.dumps(
                {"result": {"outputs": []}}).encode())
        with patch("hydra_crud.plugins.odysee._http", fake_http):
            with pytest.raises(Exception, match="no claim_id"):
                article = Post(title="A", body="b", slug="a-post",
                               post_type="article")
                plugin.create(article, dict(ctx))


class TestClaimParamsExtended:
    """Extended publish options (2026-09-06): visibility, tags override,
    description override, release time, license + license_url."""

    def test_visibility_unlisted_uses_fixed_ts(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_visibility'] = 'unlisted'
        params = plugin._claim_params(post, ctx, None)
        assert params['release_time'] == 2147483647

    def test_visibility_default_is_unlisted(self, plugin, post, ctx, fake_keyring):
        params = plugin._claim_params(post, ctx, None)
        assert params['release_time'] == 2147483647

    def test_visibility_public_with_explicit_release_time(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_visibility'] = 'public'
        ctx['odysee_release_time'] = 1700000000
        params = plugin._claim_params(post, ctx, None)
        assert params['release_time'] == 1700000000

    def test_visibility_scheduled_needs_release_time(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_visibility'] = 'scheduled'
        with pytest.raises(Exception, match='needs odysee_release_time'):
            plugin._claim_params(post, ctx, None)

    def test_scheduled_with_future_ts(self, plugin, post, ctx, fake_keyring):
        import time
        ctx['odysee_visibility'] = 'scheduled'
        ctx['odysee_release_time'] = int(time.time()) + 86400
        params = plugin._claim_params(post, ctx, None)
        assert params['release_time'] == ctx['odysee_release_time']

    def test_release_time_backdate(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_visibility'] = 'public'
        ctx['odysee_release_time'] = 1700000000
        params = plugin._claim_params(post, ctx, None)
        assert params['release_time'] == 1700000000

    def test_invalid_visibility_rejected(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_visibility'] = 'secret'
        with pytest.raises(Exception, match='odysee_visibility must be'):
            plugin._claim_params(post, ctx, None)

    def test_tags_override_no_cap(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_tags'] = 'animation, shorts, romance, comedy, art, extra'
        params = plugin._claim_params(post, ctx, None)
        assert params['tags'] == ['animation', 'shorts', 'romance',
                                  'comedy', 'art', 'extra']

    def test_description_override(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_description'] = 'Custom description text.'
        params = plugin._claim_params(post, ctx, None)
        assert params['description'] == 'Custom description text.'

    def test_license_and_url(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_license'] = 'Public Domain'
        ctx['odysee_license_url'] = 'https://unlicense.org'
        params = plugin._claim_params(post, ctx, None)
        assert params['license'] == 'Public Domain'
        assert params['license_url'] == 'https://unlicense.org'

    def test_license_url_alone_ignored(self, plugin, post, ctx, fake_keyring):
        ctx['odysee_license_url'] = 'https://unlicense.org'
        params = plugin._claim_params(post, ctx, None)
        assert 'license' not in params
        assert 'license_url' not in params

    def test_thumbnail_param_included(self, plugin, post, ctx, fake_keyring):
        params = plugin._claim_params(post, ctx, None,
                                      thumbnail_url='https://arweave.net/xyz')
        assert params['thumbnail_url'] == 'https://arweave.net/xyz'

    def test_explicit_cover_becomes_thumbnail(self, plugin, ctx, fake_keyring,
                                              monkeypatch):
        # cover_image given -> _thumbnail_url uploads it (mocked) and no
        # ffmpeg extraction runs.
        post = Post(title='T', body='b', slug='t-1', post_type='video',
                    cover_image='/some/cover.jpg')
        def fake_upload(path):
            assert str(path) == '/some/cover.jpg'
            return 'https://arweave.net/cover-uploaded'
        monkeypatch.setattr(plugin, '_upload_arweave', fake_upload)
        # /some/cover.jpg does not exist -> falls to video frame; instead
        # use a real temp file so the cover branch runs.
        cover = Path(ctx['tmp_dir'] if 'tmp_dir' in ctx else '/tmp')
        cover = Path('/tmp') / 'odysee-test-cover.jpg'
        cover.write_bytes(b'\xff\xd8JPEGDATA')
        post = Post(title='T', body='b', slug='t-1', post_type='video',
                    cover_image=str(cover))
        def fake_upload2(path):
            return 'https://arweave.net/cover-uploaded'
        monkeypatch.setattr(plugin, '_upload_arweave', fake_upload2)
        url = plugin._thumbnail_url(post, ctx, None)
        assert url == 'https://arweave.net/cover-uploaded'
        cover.unlink()


class TestRead:
    def test_read_no_url_no_id(self, plugin):
        ref = PlatformRef(platform="odysee")
        status = plugin.read(ref, {})
        assert status.exists is False

    def test_read_resolves_claim(self, plugin, monkeypatch):
        ref = PlatformRef(platform="odysee",
                          url="https://odysee.com/her-love-video",
                          post_id="feedcafe1")
        def fake_http(method, url, headers=None, data=None, timeout=120):
            return (200, {}, json.dumps({
                "result": {"lbry://her-love-video":
                           {"value": {"title": "x"}}}}).encode())
        with patch("hydra_crud.plugins.odysee._http", fake_http):
            status = plugin.read(ref, {})
        assert status.exists is True

    def test_read_missing_claim(self, plugin, monkeypatch):
        ref = PlatformRef(platform="odysee",
                          url="https://odysee.com/gone",
                          post_id="gone1")
        def fake_http(method, url, headers=None, data=None, timeout=120):
            return (200, {}, b'{"result": {"lbry://gone": {}}}')
        with patch("hydra_crud.plugins.odysee._http", fake_http):
            status = plugin.read(ref, {})
        assert status.exists is False


class TestUpdateDelete:
    def test_update_republishes_same_name(self, plugin, post, ctx, fake_keyring):
        ref = PlatformRef(platform="odysee",
                          url="https://odysee.com/my-video",
                          post_id="feedcafe1")
        with patch.object(plugin, "create") as create:
            create.return_value = PlatformRef(
                platform="odysee", post_id="feedface2", url="x")
            new_ref = plugin.update(ref, post, ctx)
        # The republished slug must match the existing claim name
        republished = create.call_args[0][0]
        assert republished.slug == "my-video"
        assert new_ref.post_id == "feedface2"
        assert new_ref.updated_at

    def test_delete_needs_claim_id(self, plugin, ctx, fake_keyring):
        ref = PlatformRef(platform="odysee")  # no post_id
        with pytest.raises(Exception, match="needs post_id"):
            plugin.delete(ref, ctx)

    def test_delete_dry_run(self, plugin, ctx, fake_keyring):
        ctx["dry_run"] = True
        ref = PlatformRef(platform="odysee", post_id="feedcafe1")
        with patch("hydra_crud.plugins.odysee._http") as http:
            plugin.delete(ref, ctx)
            http.assert_not_called()

    def test_delete_calls_abandon(self, plugin, ctx, fake_keyring):
        ref = PlatformRef(platform="odysee", post_id="feedcafe1")
        captured = {}
        def fake_http(method, url, headers=None, data=None, timeout=120):
            captured.update(method=method, url=url, data=data)
            return (200, {}, b'{"result": {}}')
        with patch("hydra_crud.plugins.odysee._http", fake_http):
            plugin.delete(ref, ctx)
        # The web client's method is stream_abandon (claim_abandon is
        # forbidden on the proxy -- verified live 2026-09-06).
        assert captured["data"] and b"stream_abandon" in captured["data"]
        assert b"feedcafe1" in captured["data"]

    def test_delete_surfaces_sdk_error(self, plugin, ctx, fake_keyring):
        """HTTP 200 with a JSON-RPC error body must FAIL, not pass silently
        (the bug that orphaned a duplicate claim trusted status alone)."""
        ref = PlatformRef(platform="odysee", post_id="feedcafe1")
        def fake_http(method, url, headers=None, data=None, timeout=120):
            return (200, {}, json.dumps(
                {"error": {"code": -32601,
                           "message": "forbidden method"}}).encode())
        with patch("hydra_crud.plugins.odysee._http", fake_http):
            with pytest.raises(Exception, match="forbidden method"):
                plugin.delete(ref, dict(ctx))
