"""Odysee CRUD plugin for hydra_crud.

Transport: api (Odysee first-party v4 publish pipeline -- the exact flow the
official web client runs, reconstructed from OdyseeTeam/odysee-frontend
web/setup/publish-v4*.ts and .env.defaults):

  1. POST {ASYN}/uploads/  (X-Lbry-Auth-Token) ->
       {"status": "upload_token_created", "payload": {"token", "location"}}
  2. TUS upload to `location` with Authorization: Bearer <token>
     (POST create + PATCH chunks, 50MB max).
  3. POST {ASYN}/  (X-Lbry-Auth-Token) JSON-RPC:
       method=stream_create (or stream_update when claim_id present),
       params.file_path = <tus upload URL>, ...claim params
     -> {"status": "query_created", "payload": {"query_id"}}
  4. GET {ASYN}/{query_id} poll: 204 = pending, 200 = done (result JSON).

ASYN = https://api.na-backend.odysee.com/api/v1/asynqueries

read -> resolve via the public proxy (no auth).
delete -> claim_abandon via the SDK proxy (wallet-scoped, auth needed).

Auth: X-Lbry-Auth-Token from keyring service 'odysee' key 'auth_token'
(captured from the logged-in Odysee browser session 2026-09-06).
Channel: keyring 'odysee'/'channel_id' (@archerships).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..model import Post, PlatformRef, PostStatus
from ..plugin import Plugin, PluginError

# First-party endpoints (odysee-frontend .env.defaults + llms.odysee.com).
ASYN_URL = "https://api.na-backend.odysee.com/api/v1/asynqueries"
PROXY_URL = "https://api.na-backend.odysee.com/api/v1/proxy"

TUS_CHUNK = 50 * 1024 * 1024   # 50 MiB -- the web client's chunk size
POLL_INITIAL_S = 2              # client waits 2s before first poll
POLL_INTERVAL_S = 10            # then every 10s
POLL_TIMEOUT_S = 30 * 60        # 30-minute confirmation budget


def _auth_token() -> str:
    """Load the Odysee auth token from the keyring (never argv/env)."""
    try:
        import keyring
        token = keyring.get_password("odysee", "auth_token")
    except Exception as exc:  # keyring backend failure
        raise PluginError(f"keyring unavailable: {exc}") from exc
    if not token:
        raise PluginError(
            "Odysee auth token not found in keyring "
            "(service 'odysee', key 'auth_token'). Capture it once from "
            "the logged-in Odysee web session and store it with:\n"
            "  python3 -c \"import keyring; "
            "keyring.set_password('odysee', 'auth_token', 'TOKEN')\"")
    return token


def _channel_id(ctx: dict) -> str:
    """The @channel claim id to publish under (keyring 'channel_id')."""
    try:
        import keyring
        cid = keyring.get_password("odysee", "channel_id")
    except Exception:
        cid = None
    return cid or ctx.get("odysee_channel_id", "")


def _http(method: str, url: str, headers: dict,
          data: bytes | None = None, timeout: int = 120) -> tuple[int, dict, bytes]:
    """One HTTP call; returns (status, headers, body)."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


class OdyseePlugin(Plugin):
    platform = "odysee"
    display_name = "Odysee"
    post_types = {"video", "article"}
    destinations = {"channel"}  # Odysee's only destination surface
    anonymous = False
    transport = "api"

    # ------------------------------------------------------------------
    # v4 publish pipeline steps
    # ------------------------------------------------------------------

    def _request_upload_token(self, token: str) -> tuple[str, str]:
        """Step 1: create an upload token+location for a TUS upload."""
        status, _, body = _http(
            "POST", f"{ASYN_URL}/uploads/",
            headers={"X-Lbry-Auth-Token": token,
                     "Content-Type": "application/json"})
        if status != 200:
            raise PluginError(
                f"upload-token request HTTP {status}: {body[:300]!r}")
        d = json.loads(body.decode("utf-8"))
        payload = d.get("payload") or {}
        if d.get("status") != "upload_token_created" or not payload.get("location"):
            raise PluginError(f"unexpected upload-token response: {d}")
        return payload["token"], payload["location"]

    def _tus_upload(self, bearer: str, location: str, file_path: Path) -> str:
        """Step 2: TUS create + PATCH loop. Returns the final upload URL."""
        size = file_path.stat().st_size

        # TUS create (0-byte POST with metadata)
        status, headers, body = _http(
            "POST", location,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Tus-Resumable": "1.0.0",
                "Upload-Length": str(size),
                "Upload-Metadata":
                    f"filename {self._b64(file_path.name)}",
            })
        if status not in (200, 201):
            raise PluginError(f"TUS create HTTP {status}: {body[:300]!r}")
        upload_url = headers.get("Location") or headers.get("location")
        if not upload_url:
            raise PluginError("TUS create returned no Location header")

        # PATCH the bytes
        offset = 0
        with open(file_path, "rb") as fh:
            while offset < size:
                chunk = fh.read(TUS_CHUNK)
                status, headers, body = _http(
                    "PATCH", upload_url,
                    headers={
                        "Authorization": f"Bearer {bearer}",
                        "Tus-Resumable": "1.0.0",
                        "Content-Type": "application/offset+octet-stream",
                        "Upload-Offset": str(offset),
                    },
                    data=chunk, timeout=600)
                if status not in (200, 204):
                    raise PluginError(
                        f"TUS PATCH at {offset} HTTP {status}: {body[:300]!r}")
                offset = int(headers.get("Upload-Offset", offset + len(chunk)))
        return upload_url

    @staticmethod
    def _b64(s: str) -> str:
        import base64
        return base64.b64encode(s.encode()).decode()

    def _stream_create(self, token: str, file_url: str,
                       params: dict) -> int:
        """Step 3: JSON-RPC stream_create -> query_id."""
        rpc = {"jsonrpc": "2.0",
               "method": "stream_create" if not params.get("claim_id")
                         else "stream_update",
               "params": {**params, "file_path": file_url},
               "id": int(time.time() * 1000)}
        status, _, body = _http(
            "POST", f"{ASYN_URL}/",
            headers={"X-Lbry-Auth-Token": token,
                     "Content-Type": "application/json"},
            data=json.dumps(rpc).encode("utf-8"))
        if status not in (200, 201):
            raise PluginError(f"stream_create HTTP {status}: {body[:300]!r}")
        d = json.loads(body.decode("utf-8"))
        if d.get("status") != "query_created":
            raise PluginError(f"unexpected query response: {d}")
        return d["payload"]["query_id"]  # int or hex string; opaque to us

    def _poll_query(self, token: str, query_id: int) -> dict:
        """Step 4: poll the async query until done (200) or timeout."""
        deadline = time.monotonic() + POLL_TIMEOUT_S
        time.sleep(POLL_INITIAL_S)
        while True:
            status, _, body = _http(
                "GET", f"{ASYN_URL}/{query_id}",
                headers={"X-Lbry-Auth-Token": token})
            if status == 200 and body:
                d = json.loads(body.decode("utf-8"))
                result = d.get("result") or d.get("payload") or d
                if d.get("status") == "error" or (isinstance(result, dict)
                                                 and result.get("error")):
                    raise PluginError(f"stream_create query error: {d}")
                return result
            if status == 404:
                raise PluginError(f"query {query_id} not found (not owned?)")
            if time.monotonic() > deadline:
                raise PluginError(
                    f"poll timed out after {POLL_TIMEOUT_S}s (query {query_id})")
            time.sleep(POLL_INTERVAL_S)

    # ------------------------------------------------------------------
    # Plugin contract
    # ------------------------------------------------------------------

    def _thumbnail_url(self, post: Post, ctx: dict,
                       video: Path | None) -> str:
        """Thumbnail URL for the claim: cover_image if given, else a frame
        extracted from the video and uploaded to Arweave (free tier,
        <100KiB WebP/JPG). Odysee does NOT auto-generate thumbnails
        server-side -- claims without thumbnail_url show a gray
        placeholder everywhere (observed live 2026-09-06)."""
        import subprocess as sp

        # Explicit cover wins
        if post.cover_image:
            cover = Path(post.cover_image)
            if cover.exists() and cover.stat().st_size <= 100 * 1024:
                url = self._upload_arweave(cover)
                if url:
                    return url
            elif cover.exists():
                # oversize -> shrink via compress.py convention is out of
                # scope here; use the video frame instead
                pass

        if video is None or not video.exists():
            return ""

        # Extract a frame at 3s (or 10% in for very short clips), 640px wide.
        dur_probe = sp.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', str(video)],
            capture_output=True, text=True, timeout=60)
        try:
            dur = float(dur_probe.stdout.strip() or '0')
        except ValueError:
            dur = 0
        seek = min(3.0, dur * 0.1) if dur else 0

        tmp_dir = Path(ctx.get('tmp_dir') or '/tmp')
        tmp_dir.mkdir(parents=True, exist_ok=True)
        thumb = tmp_dir / f'odysee-thumb-{post.slug[:50]}.jpg'
        r = sp.run(
            ['ffmpeg', '-y', '-ss', f'{seek:.2f}', '-i', str(video),
             '-frames:v', '1', '-vf', 'scale=640:-2', str(thumb)],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not thumb.exists() or thumb.stat().st_size == 0:
            print(f'  WARNING: thumbnail extraction failed: {r.stderr[-200:]}')
            return ''
        if thumb.stat().st_size > 100 * 1024:
            print('  WARNING: extracted thumbnail over 100KiB; skipping upload')
            return ''
        url = self._upload_arweave(thumb)
        thumb.unlink(missing_ok=True)
        return url or ''

    def _upload_arweave(self, path: Path) -> str:
        """Upload a small file to Arweave via arweave-upload.mjs; returns the
        https://arweave.net/{txId} URL or '' on failure (non-fatal)."""
        import subprocess as sp
        script = Path.home() / 'av' / 'bin' / 'archerships' / 'arweave-upload.mjs'
        if not script.exists():
            return ''
        try:
            r = sp.run(
                ['node', str(script), str(path)],
                capture_output=True, text=True, timeout=300,
                cwd=str(Path.home() / 'av' / 'prj' / 'archerships.com'))
        except Exception:
            return ''
        if r.returncode != 0:
            return ''
        url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''
        return url if url.startswith('https://arweave.net/') else ''

    def _claim_params(self, post: Post, ctx: dict,
                      file_path: Path | None, thumbnail_url: str = '') -> dict:
        """SDK stream_create params (the lbrynet publish flag set as JSON).

        Extended fields (2026-09-06, user request) come from the ctx dict:
          thumbnail_url  -- set by create() via _thumbnail_url
          odysee_visibility    -- 'public' (default) | 'unlisted' | 'scheduled'
                          (implemented as release_time per the web client:
                          unlisted = 2147483647 hides from feeds/notifications;
                          scheduled = future unix ts holds the claim back)
          odysee_tags    -- comma list overriding post.tags (no 5-cap)
          odysee_description -- overrides post.body
          odysee_release_time -- explicit unix ts for public/scheduled
          odysee_license -- license name (e.g. 'Public Domain', 'None',
                            'Copyrighted'), or custom text
          odysee_license_url -- optional license URL (with license)
        """
        import re
        import time as _time
        name = re.sub(r"[^a-z0-9-]", "", post.slug.lower())[:60]

        # Description: explicit override, else the post body.
        description = str(ctx.get("odysee_description") or post.body or "")

        # Tags: explicit comma list, else post.tags. No cap (client sends
        # up to ~10; SDK accepts more).
        raw_tags = ctx.get("odysee_tags")
        if raw_tags:
            tags = [t.strip().lower() for t in str(raw_tags).split(",") if t.strip()]
        else:
            tags = [t.strip().lower() for t in post.tags if t.strip()]

        params = {
            "name": name,
            "title": post.title,
            "description": description,
            "bid": "0.001",                     # minimal, per sync-to-odysee
            "tags": tags,
            "blocking": True,
            # Same-name revisions are the plugin's update path; without this
            # flag the SDK rejects a re-publish of an existing claim name
            # (observed live 2026-09-06: "Use --allow-duplicate-name").
            "allow_duplicate_name": True,
        }

        if thumbnail_url:
            params["thumbnail_url"] = thumbnail_url

        # Visibility via release_time (the web client's encoding).
        # Default unlisted per 2026-09-06 -- keeps the claim out of feeds
        # and search while remaining link-watchable. 'private' removed as an
        # option (it is only a stub tag on Odysee; no content gating).
        UNLISTED_TS = 2147483647   # backend reads future-dates as unlisted
        visibility = str(ctx.get("odysee_visibility") or "unlisted").lower()
        if visibility not in ("public", "unlisted", "scheduled"):
            raise PluginError(
                f"odysee_visibility must be public|unlisted|scheduled, "
                f"got {visibility!r}")
        release_time = ctx.get("odysee_release_time")
        if visibility == "unlisted":
            params["release_time"] = UNLISTED_TS
        elif release_time is not None:
            params["release_time"] = int(release_time)
        elif visibility == "scheduled":
            raise PluginError(
                "odysee_visibility=scheduled needs odysee_release_time "
                "(future unix ts)")
        else:
            params["release_time"] = int(_time.time())  # public: now

        # License (SDK field: license, optional license_url).
        license_ = ctx.get("odysee_license")
        if license_:
            params["license"] = str(license_)
            license_url = ctx.get("odysee_license_url")
            if license_url:
                params["license_url"] = str(license_url)

        channel_id = _channel_id(ctx)
        if channel_id:
            params["channel_id"] = channel_id
        return params

    def _video_file(self, post: Post, ctx: dict) -> Path | None:
        """Locate the video for a video post: essay dir media, else none."""
        essay_dir = Path(ctx.get("essay_dir", "") or "")
        if not essay_dir.is_dir():
            return None
        for pattern in ("*.mp4", "*.mkv", "*.webm"):
            for p in sorted(essay_dir.rglob(pattern)):
                return p
        return None

    def create(self, post: Post, ctx: dict) -> PlatformRef:
        token = _auth_token()
        dry_run = bool(ctx.get("dry_run", False))

        # Dry-run short-circuits BEFORE the video lookup: a preview of
        # what would publish must not require the media file to exist.
        params_preview = self._claim_params(post, ctx, None)
        claim_url = f"https://odysee.com/{params_preview['name']}"
        if dry_run:
            print(f"  [dry-run] odysee publish params: "
                  f"{json.dumps(params_preview, indent=2)}")
            return PlatformRef(
                platform="odysee", account=ctx.get("account", "archerships"),
                destination="channel", url=claim_url,
                post_type=post.post_type,
                created_at=PlatformRef.today(), updated_at=PlatformRef.today(),
            )

        video = None
        if post.post_type == "video":
            video = self._video_file(post, ctx)
            if video is None:
                raise PluginError(
                    f"video post but no video file found in "
                    f"{ctx.get('essay_dir', '(no essay_dir)')} "
                    f"(looked for *.mp4/*.mkv/*.webm)")

        params = self._claim_params(post, ctx, video,
                                    thumbnail_url=self._thumbnail_url(
                                        post, ctx, video))

        if video is not None:
            # v4 pipeline: token -> TUS upload -> stream_create(file_path=upload URL) -> poll
            bearer, location = self._request_upload_token(token)
            upload_url = self._tus_upload(bearer, location, video)
            query_id = self._stream_create(token, upload_url, params)
            result = self._poll_query(token, query_id)
        else:
            # Non-video (article-style): direct SDK publish via proxy.
            status, _, body = _http(
                "POST", PROXY_URL,
                headers={"X-Lbry-Auth-Token": token,
                         "Content-Type": "application/json"},
                data=json.dumps({"method": "publish",
                                 "params": params}).encode("utf-8"))
            if status != 200:
                raise PluginError(f"proxy publish HTTP {status}: {body[:300]!r}")
            result = json.loads(body.decode("utf-8"))

        # Result shape varies by path: the poll returns the SDK result
        # (outputs at top level); the direct proxy publish wraps it under
        # "result". Handle both.
        outputs = None
        if isinstance(result, dict):
            outputs = result.get("outputs") \
                or (result.get("result") or {}).get("outputs") \
                or ((result.get("result") or {}).get("result") or {}).get("outputs")
        claim_id, final_url = "", claim_url
        if outputs:
            seq = outputs if isinstance(outputs, list) else list(outputs.values())
            for out in seq:
                claim_id = out.get("claim_id", claim_id)
                pu = out.get("permanent_url", "")
                if pu:
                    final_url = pu.replace("lbry://", "https://odysee.com/")
        if not claim_id and isinstance(result, dict):
            inner = result.get("result") or {}
            claim_id = inner.get("claim_id", "") or result.get("claim_id", "")
        if not claim_id:
            raise PluginError(
                f"publish succeeded but no claim_id in response: "
                f"{json.dumps(result or {})[:500]}")

        return PlatformRef(
            platform="odysee", account=ctx.get("account", "archerships"),
            destination="channel", url=final_url, post_id=str(claim_id),
            post_type=post.post_type,
            created_at=PlatformRef.today(), updated_at=PlatformRef.today(),
        )

    def read(self, ref: PlatformRef, ctx: dict) -> PostStatus:
        """Resolve the claim via the PUBLIC proxy (no auth needed)."""
        target = f"lbry://{ref.url.replace('https://odysee.com/', '')}" \
            if ref.url else (f"lbry://{ref.post_id}" if ref.post_id else "")
        if not target:
            return PostStatus(exists=False, post_id=ref.post_id,
                              note="no url or post_id recorded")
        q = urllib.parse.quote(target)
        status, _, body = _http(
            "GET", f"{PROXY_URL}?m=resolve&uri={q}",
            headers={"Content-Type": "application/json"})
        if status != 200:
            return PostStatus(exists=False, url=ref.url, post_id=ref.post_id,
                              note=f"resolve HTTP {status}")
        data = json.loads(body.decode("utf-8"))
        claim = (data.get("result") or {}).get(target) or {}
        if claim.get("value"):
            return PostStatus(exists=True, url=ref.url, post_id=ref.post_id)
        return PostStatus(exists=False, url=ref.url, post_id=ref.post_id,
                          note="claim not found in resolve")

    def update(self, ref: PlatformRef, post: Post, ctx: dict) -> PlatformRef:
        """Odysee claims are immutable on-chain; update = publish a new
        revision under the SAME claim name (stream_update when the
        backend recognizes the claim_id, else same-name stream_create)."""
        ref_copy = PlatformRef(**{k: v for k, v in ref.to_dict().items()})
        ctx = dict(ctx)
        # Force the same claim name as the existing post so the new
        # revision supersedes it at the same URL.
        post_dict = post.to_dict()
        post_dict["slug"] = ref.url.rstrip("/").split("/")[-1]
        new_ref = self.create(Post(**post_dict), ctx)
        ref_copy.post_id = new_ref.post_id
        ref_copy.updated_at = PlatformRef.today()
        return ref_copy

    def delete(self, ref: PlatformRef, ctx: dict) -> None:
        """Abandon the claim via the SDK proxy (wallet-scoped, auth needed).
        The blockchain record remains but the claim becomes unresolvable
        and content hosting stops -- this is Odysee's real delete.

        Method note (verified live 2026-09-06): the proxy forbids
        'claim_abandon' (-32601 forbidden method); the web client's actual
        method is 'stream_abandon'. Wallet-mutating calls also return
        HTTP 200 with a JSON-RPC error in the body when they fail -- both
        must be checked (the publish bug that orphaned a duplicate claim
        came from trusting the HTTP status alone)."""
        if not ref.post_id:
            raise PluginError("odysee delete needs post_id (claim_id) in the ref")
        token = _auth_token()
        if ctx.get("dry_run", False):
            print(f"  [dry-run] odysee abandon claim {ref.post_id}")
            return
        status, _, body = _http(
            "POST", PROXY_URL,
            headers={"X-Lbry-Auth-Token": token,
                     "Content-Type": "application/json"},
            data=json.dumps({"method": "stream_abandon",
                             "params": {"claim_id": ref.post_id}}).encode("utf-8"))
        if status != 200:
            raise PluginError(f"stream_abandon HTTP {status}: {body[:300]!r}")
        d = json.loads(body.decode("utf-8"))
        if d.get("error"):
            raise PluginError(
                f"stream_abandon SDK error: {d['error'].get('message')} "
                f"(code {d['error'].get('code')})")


def _register() -> None:
    from ..registry import register
    register(OdyseePlugin)


_register()
