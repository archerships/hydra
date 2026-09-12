"""Plugin interface for hydra_crud platform plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .model import Post, PlatformRef, PostStatus


class PluginError(Exception):
    """Raised when a platform operation fails."""


class Plugin(ABC):
    """One platform's CRUD implementation.

    Subclasses set class attributes (platform, display_name, post_types,
    destinations, anonymous, transport) and implement the CRUD methods.
    Methods that a platform cannot support (e.g. arweave delete) raise
    NotImplementedError with an explanation.
    """

    platform: str = ""
    display_name: str = ""
    post_types: set[str] = {"article"}
    destinations: set[str] = {"profile"}
    anonymous: bool = False
    transport: str = "cdp"          # cdp | api | cli | relay | git

    @abstractmethod
    def create(self, post: Post, ctx: dict) -> PlatformRef:
        """Publish a new post. Returns the platform ref (id + url)."""

    def read(self, ref: PlatformRef, ctx: dict) -> PostStatus:
        """Check whether the post still exists and fetch its status.

        Default: if url is present, assume exists (subclasses override with
        a real check when the platform supports it).
        """
        return PostStatus(exists=bool(ref.url), url=ref.url, post_id=ref.post_id)

    def update(self, ref: PlatformRef, post: Post, ctx: dict) -> PlatformRef:
        """Update an existing post in place. Returns the (possibly new) ref."""
        raise NotImplementedError(
            f"{self.platform} plugin does not support update yet")

    @abstractmethod
    def delete(self, ref: PlatformRef, ctx: dict) -> None:
        """Delete a post on the platform. Irreversible on most platforms."""

    def list(self, ctx: dict) -> list[PlatformRef]:
        """List all posts by the account (optional; default unsupported)."""
        raise NotImplementedError(
            f"{self.platform} plugin does not support list yet")
