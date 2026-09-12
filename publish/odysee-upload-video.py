#!/usr/bin/env python3
"""odysee-upload-video.py -- publish ONE video file to Odysee (unlisted).

Bridge for spadd: downloads are done by spadd (yt-dlp); this script takes the
resulting local video file, builds a hydra_crud Post, and calls the Odysee
plugin's create() so the single live-verified publish implementation is reused
(no duplicated publish logic). Prints the resulting https://odysee.com/ URL on
stdout (last line), or nothing meaningful on failure.

Usage:
  ~/av/venv/hydra/bin/python3 bin/hydra/publish/odysee-upload-video.py \
      --video /abs/path.mp4 --title "..." --slug cool-slug \
      [--description "..."] [--tags "a,b,c"] [--account archerships] \
      [--dry-run]

The plugin publishes UNLISTED by default and uploads the thumbnail itself
(explicit cover or an extracted video frame -> Arweave). Requires Odysee
credentials in the keyring (service 'odysee': auth_token, channel_id).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make hydra_crud importable: this file lives in bin/hydra/publish/.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hydra_crud.model import Post          # noqa: E402
from hydra_crud.plugins.odysee import OdyseePlugin  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish one video to Odysee (unlisted)")
    ap.add_argument("--video", required=True, help="absolute path to the video file")
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--account", default="archerships")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    video = Path(args.video).expanduser()
    if not video.is_file():
        print(f"odysee-upload-video: video not found: {video}", file=sys.stderr)
        return 2

    tags = [t.strip().lower() for t in args.tags.split(",") if t.strip()]
    post = Post(
        title=args.title,
        body=args.description or args.title,
        tags=tags,
        slug=args.slug,
        post_type="video",
    )
    # The plugin locates the video file inside ctx['essay_dir'] and writes
    # the extracted thumbnail under ctx['tmp_dir']. Point both at the video's
    # parent so _video_file finds it.
    video_dir = str(video.parent)
    ctx = {
        "account": args.account,
        "essay_dir": video_dir,
        "tmp_dir": video_dir,
        "dry_run": bool(args.dry_run),
        "odysee_visibility": "unlisted",  # explicit; plugin default
    }

    try:
        ref = OdyseePlugin().create(post, ctx)
    except Exception as exc:  # noqa: BLE001 -- report and let spadd fall back
        print(f"odysee-upload-video: FAILED: {exc}", file=sys.stderr)
        return 1

    print(ref.url or ref.post_id or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
