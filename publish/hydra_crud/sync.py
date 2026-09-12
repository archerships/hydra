"""sync.py -- the SRC -> DST sync engine (D9).

Shared logic for `hydra-publish sync`: given a source Post and a list of
destination plugins, upsert the post into each DST:
  - DST has a ref  -> plugin.update(ref, post)
  - DST has no ref -> plugin.create(post)
  - conflict rule  -> never clobber a newer DST post (compare timestamps)
  - dry_run        -> report create-vs-update without calling plugins

Kept separate from the orchestrator so it is unit-testable with stub
plugins (Task 1.9).
"""

from __future__ import annotations

from typing import Callable

from .model import Post, PlatformRef


class SyncResult:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.updated: list[str] = []
        self.skipped: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def __bool__(self) -> bool:
        return not self.failed


def sync_to_dst(
    post: Post,
    dst: str,
    plugin,
    ref: PlatformRef | None,
    dry_run: bool = False,
    record_ref: Callable[[str, dict], None] | None = None,
    now: str | None = None,
) -> str:
    """Upsert `post` into one destination. Returns the action taken.

    Actions: 'created', 'updated', 'skipped', or raises on failure.
    `record_ref` is called with (platform, ref_dict) after a real op.
    """
    from datetime import date

    now = now or date.today().isoformat()

    if ref is None:
        new_ref = plugin.create(post, {"dry_run": dry_run})
        action = "created"
    else:
        # Conflict rule: never clobber a newer DST post.
        src_date = getattr(post, "updated_at", "") or ""
        dst_date = getattr(ref, "updated_at", "") or ""
        if src_date and dst_date and src_date < dst_date:
            return "skipped"
        new_ref = plugin.update(ref, post, {"dry_run": dry_run})
        action = "updated"

    if not dry_run and record_ref is not None:
        record_ref(dst, new_ref.to_dict())
    return action
