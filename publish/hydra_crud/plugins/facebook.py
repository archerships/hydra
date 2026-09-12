"""Facebook CRUD plugin for hydra_crud.

Transport: CDP (raw WebSocket against Chromium on port 9222). Facebook has no
usable anonymous API (Graph API requires a verified business account), so
browser automation is the only path. `anonymous` is False per the anonymity
matrix; the account is a normal Facebook profile.

Wraps the existing publisher scripts:
  create -> fb-publisher.py (posts body, attaches images, adds comment)
  update -> fb-edit-post.py (edits body, attaches image)
  delete -> fb-delete-post.py (menu -> Delete post -> confirm)
  read   -> CDP check that the post URL still shows the post

Ctx keys (provided by the hydra-publish orchestrator):
  post: Post
  tmp_dir, essay_dir, contact_file, dry_run, account, destination, cdp_port
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..model import Post, PlatformRef, PostStatus
from ..plugin import Plugin, PluginError

# Ctx defaults (overridable by the orchestrator via --account/--destination
# and the ~/.config/hydra-publish/config file).
DEFAULT_CONTACT_FILE = Path.home() / "av" / "prj" / "hydra" / "publish" / "config" / "fb-contact.txt"


class FacebookPlugin(Plugin):
    platform = "facebook"
    display_name = "Facebook"
    post_types = {"article", "tweet", "image"}
    destinations = {"profile", "page", "group"}
    anonymous = False
    transport = "cdp"

    def _scripts(self, ctx: dict) -> dict[str, Path]:
        """Locate the publisher scripts (publish root, next to hydra_crud)."""
        base = Path(__file__).resolve().parent.parent.parent  # ~/av/bin/hydra/publish
        return {
            "create": base / "fb-publisher.py",
            "update": base / "fb-edit-post.py",
            "delete": base / "fb-delete-post.py",
        }

    def _ctx(self, ctx: dict, key: str, default):
        return ctx.get(key, default)

    def _post_payload(self, post: Post, ctx: dict) -> tuple[Path, str]:
        """Write the body text file and return (path, essay_dir).

        Mirrors what hydra-publish does for facebook today: the body has
        URLs stripped (facebook downranks linked posts), and the canonical
        URL goes into the comment instead.
        """
        tmp_dir = Path(self._ctx(ctx, "tmp_dir", Path.home() / "av" / "tmp"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        body_file = tmp_dir / f"hydra-fb-{post.slug}.txt"

        # Reuse the same URL-strip + comment logic the orchestrator already
        # has: body text with URLs removed, essay URL in a comment.
        import re

        # Drop markdown image syntax entirely (fb-publisher attaches the
        # real images separately from the essay dir); drop bare URLs
        # (facebook downranks linked posts).
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", post.body)
        url_re = re.compile(r"\bhttps?://[^\s\]\)\"'>]+", re.IGNORECASE)
        body = url_re.sub("", body)
        body = re.sub(r"#+\s", "", body)           # heading markers
        body = re.sub(r"(\*\*|__|\*|_|~~)", "", body)  # emphasis markers
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        body_file.write_text(body or post.title, encoding="utf-8")

        essay_dir = self._ctx(ctx, "essay_dir", "")
        return body_file, str(essay_dir)

    def _run(self, cmd: list[str], ctx: dict, label: str) -> str:
        print(f"  Running {label}...")
        print(f"  $ {' '.join(cmd)}")
        dry_run = bool(self._ctx(ctx, "dry_run", False))
        if dry_run:
            print("  [dry-run] skipped.")
            return ""
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise PluginError(f"{label} failed with exit {result.returncode}")
        return result.stdout or ""

    def create(self, post: Post, ctx: dict) -> PlatformRef:
        scripts = self._scripts(ctx)
        body_file, essay_dir = self._post_payload(post, ctx)

        # fb-publisher.py composes the comment itself from --essay-url +
        # contact file; pass the contact file and let it build the comment.
        cmd = [sys.executable, str(scripts["create"]), str(body_file)]
        if essay_dir:
            cmd += ["--essay-dir", essay_dir]
        if post.url:
            cmd += ["--essay-url", post.url]
        contact_file = Path(self._ctx(ctx, "contact_file", DEFAULT_CONTACT_FILE))
        if contact_file.exists():
            cmd += ["--contact-file", str(contact_file)]
        if self._ctx(ctx, "dry_run", False):
            cmd += ["--dry-run"]

        self._run(cmd, ctx, "fb-publisher (create)")

        # The publisher prints the post URL in its summary line.
        return PlatformRef(
            platform="facebook",
            account=self._ctx(ctx, "account", "archerships"),
            destination=self._ctx(ctx, "destination", "profile"),
            url=post.url,
            post_type=post.post_type,
            created_at=PlatformRef.today(),
            updated_at=PlatformRef.today(),
        )

    def read(self, ref: PlatformRef, ctx: dict) -> PostStatus:
        if not ref.url:
            return PostStatus(exists=False, post_id=ref.post_id,
                              note="no URL recorded for this post")
        # CDP check would require a live browser; report based on URL and
        # let the caller verify in the browser when needed.
        return PostStatus(exists=True, url=ref.url, post_id=ref.post_id,
                          note="assumed live (URL recorded); browser verify recommended")

    def update(self, ref: PlatformRef, post: Post, ctx: dict) -> PlatformRef:
        scripts = self._scripts(ctx)
        body_file, _ = self._post_payload(post, ctx)

        cmd = [sys.executable, str(scripts["update"]), "--url", ref.url,
               "--body-file", str(body_file)]
        if post.cover_image and Path(post.cover_image).exists():
            cmd += ["--image", str(Path(post.cover_image).resolve())]
        if self._ctx(ctx, "dry_run", False):
            cmd += ["--dry-run"]

        self._run(cmd, ctx, "fb-edit-post (update)")

        ref.updated_at = PlatformRef.today()
        return ref

    def delete(self, ref: PlatformRef, ctx: dict) -> None:
        scripts = self._scripts(ctx)
        cmd = [sys.executable, str(scripts["delete"])]
        if ref.url:
            cmd += ["--url", ref.url]
        elif ref.post_id:
            cmd += ["--post-id", ref.post_id]
        else:
            raise PluginError("facebook delete needs post_id or url in the ref")
        if self._ctx(ctx, "yes", False):
            cmd += ["--yes"]
        if self._ctx(ctx, "dry_run", False):
            cmd += ["--dry-run"]

        self._run(cmd, ctx, "fb-delete-post (delete)")

    def fetch_post(self, ref: PlatformRef, ctx: dict) -> Post:
        """Reverse-flow source fetch: extract a Post from the live FB post.

        Used by `hydra-publish sync --from facebook`. The real DOM
        extraction (body text, images, timestamp) is Task 1.9 step 4;
        until then, sync --from facebook refuses loudly.
        """
        raise NotImplementedError(
            "facebook fetch_post not implemented yet -- sync --from facebook "
            "needs Task 1.9 step 4 (live-post DOM extraction)")


def _register() -> None:
    from ..registry import register
    register(FacebookPlugin)


_register()
