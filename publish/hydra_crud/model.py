"""Core data models for hydra_crud: Post, PlatformRef, PostStatus."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date


@dataclass
class Post:
    """Normalized content to publish.

    Fields mirror what a platform needs: title, body (markdown or plain),
    cover image path, tags, canonical URL, source slug, and the user-facing
    post type (tweet/article/list/image/video -- see posttype.py).
    """
    title: str = ""
    body: str = ""
    cover_image: str = ""          # local path or URL
    tags: list[str] = field(default_factory=list)
    url: str = ""                  # canonical URL on archerships.com
    slug: str = ""
    post_type: str = "article"     # tweet | article | list | image | video
    updated_at: str = ""           # source post timestamp (sync conflict rule)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlatformRef:
    """A published post's identity on one platform -- stored in frontmatter.

    `id` is the platform-native identifier (post id, article id, event id);
    `url` is the human-facing URL. Both are needed for update/delete.
    """
    platform: str
    account: str = ""
    destination: str = "profile"   # profile | page | group | subreddit
    post_id: str = ""
    url: str = ""
    post_type: str = "article"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def today() -> str:
        return date.today().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlatformRef":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def __eq__(self, other) -> bool:
        if not isinstance(other, PlatformRef):
            return NotImplemented
        return self.to_dict() == other.to_dict()


@dataclass
class PostStatus:
    """Result of a read operation."""
    exists: bool
    url: str = ""
    post_id: str = ""
    title: str = ""
    body_preview: str = ""
    updated_at: str = ""
    note: str = ""
