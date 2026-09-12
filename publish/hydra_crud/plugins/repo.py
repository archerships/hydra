"""Repo CRUD plugin for hydra_crud -- the source of truth.

The repo is the markdown + images under ~/av/doc/posts/ that generate
archerships.com. Per D10, these files are THE source of truth; the
rendered .html and every social platform are derived copies.

create-from-Post:  new SLUG/ dir + SLUG.md (frontmatter + body) + copy images
update-from-Post:  rewrite SLUG.md body/frontmatter
read:              parse SLUG.md + list images
delete:            ARCHIVE ONLY -- never delete source files or images
                   (never-delete-images rule); move to an archive dir.
fetch_post:        reverse-flow source: build a Post from repo files.
"""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

from ..model import Post, PlatformRef, PostStatus
from ..plugin import Plugin, PluginError

# Defaults; overridable via ctx (posts_dir, archive_dir).
DEFAULT_POSTS_DIR = Path.home() / "av" / "doc" / "posts"
DEFAULT_ARCHIVE_DIR = Path.home() / "av" / "doc" / "posts" / "_archive"


def slugify(title: str) -> str:
    """Lowercase, hyphen-only slug from a title (date prefix added by caller)."""
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (fm_dict, body). Raises ValueError if no frontmatter."""
    if not text.startswith("---"):
        raise ValueError("No frontmatter block found")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("Incomplete frontmatter block")
    import yaml
    fm = yaml.safe_load(parts[1]) or {}
    return fm, parts[2]


class RepoPlugin(Plugin):
    platform = "repo"
    display_name = "Repo (md + images source of truth)"
    post_types = {"article", "tweet", "image", "list", "video"}
    destinations = {"profile"}          # repo has no destination concept
    anonymous = True
    transport = "git"

    def _posts_dir(self, ctx: dict) -> Path:
        return Path(ctx.get("posts_dir", DEFAULT_POSTS_DIR))

    def _archive_dir(self, ctx: dict) -> Path:
        return Path(ctx.get("archive_dir", DEFAULT_ARCHIVE_DIR))

    def _slug_dir(self, ctx: dict, slug: str) -> Path:
        return self._posts_dir(ctx) / slug

    # -- read -----------------------------------------------------------

    def read(self, ref: PlatformRef, ctx: dict) -> PostStatus:
        slug = ref.post_id or Path(ref.url or "").stem
        d = self._slug_dir(ctx, slug)
        md = d / f"{slug}.md"
        if md.exists():
            return PostStatus(exists=True, url=f"https://archerships.com/essays/{slug}.html",
                              post_id=slug, title=slug, note="repo md present")
        return PostStatus(exists=False, post_id=slug, note="repo md missing")

    def fetch_post(self, ref: PlatformRef, ctx: dict) -> Post:
        """Build a Post from the repo files (used by sync --from repo)."""
        slug = ref.post_id or Path(ref.url or "").stem
        d = self._slug_dir(ctx, slug)
        md = d / f"{slug}.md"
        if not md.exists():
            raise PluginError(f"repo post not found: {md}")
        text = md.read_text(encoding="utf-8")
        fm, body = _split_frontmatter(text)
        cover = ""
        hero = fm.get("hero_image")
        if hero:
            candidate = d / str(hero)
            if candidate.exists():
                cover = str(candidate)
        return Post(
            title=fm.get("title", slug),
            body=body.strip(),
            cover_image=cover,
            tags=fm.get("tags") or [],
            url=f"https://archerships.com/essays/{slug}.html",
            slug=slug,
            post_type=fm.get("post_type", "article"),
            updated_at=fm.get("date", ""),
        )

    # -- create ---------------------------------------------------------

    def create(self, post: Post, ctx: dict) -> PlatformRef:
        posts_dir = self._posts_dir(ctx)
        slug = post.slug or (date.today().isoformat() + "-" + slugify(post.title))
        d = posts_dir / slug
        if d.exists():
            raise PluginError(f"repo post dir already exists: {d}")

        d.mkdir(parents=True, exist_ok=False)
        # Copy cover / referenced images into the post dir (img/ subdir).
        img_dir = d / "img"
        if post.cover_image and Path(post.cover_image).exists():
            img_dir.mkdir(exist_ok=True)
            target = img_dir / Path(post.cover_image).name
            shutil.copy2(post.cover_image, target)

        frontmatter = {
            "type": "long-form",
            "purpose": "explainer",
            "title": post.title,
            "subtitle": "",
            "description": f"{post.title} -- archerships",
            "date": date.today().isoformat(),
            "section": "tech",
            "tags": post.tags or [],
            "ai_percentage": 0,
            "published_at": [],
        }
        if post.cover_image:
            frontmatter["hero_image"] = f"img/{Path(post.cover_image).name}"
        import yaml
        fm_yaml = yaml.dump(frontmatter, default_flow_style=False,
                            allow_unicode=True, sort_keys=False).rstrip("\n")
        (d / f"{slug}.md").write_text(f"---\n{fm_yaml}\n---\n\n{post.body}\n",
                                      encoding="utf-8")
        return PlatformRef(
            platform="repo",
            post_id=slug,
            url=f"https://archerships.com/essays/{slug}.html",
            post_type=post.post_type,
            created_at=date.today().isoformat(),
            updated_at=date.today().isoformat(),
        )

    # -- update ---------------------------------------------------------

    def update(self, ref: PlatformRef, post: Post, ctx: dict) -> PlatformRef:
        slug = ref.post_id or Path(ref.url or "").stem
        d = self._slug_dir(ctx, slug)
        md = d / f"{slug}.md"
        if not md.exists():
            raise PluginError(f"repo post not found: {md}")
        text = md.read_text(encoding="utf-8")
        fm, _body = _split_frontmatter(text)
        fm["title"] = post.title
        if post.tags:
            fm["tags"] = post.tags
        import yaml
        fm_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True,
                            sort_keys=False).rstrip("\n")
        md.write_text(f"---\n{fm_yaml}\n---\n\n{post.body}\n", encoding="utf-8")
        ref.updated_at = date.today().isoformat()
        return ref

    # -- delete (archive only) ------------------------------------------

    def delete(self, ref: PlatformRef, ctx: dict) -> None:
        """Archive the post dir. NEVER deletes source files or images."""
        slug = ref.post_id or Path(ref.url or "").stem
        d = self._slug_dir(ctx, slug)
        if not d.exists():
            raise PluginError(f"repo post dir not found: {d}")
        archive = self._archive_dir(ctx)
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / slug
        if target.exists():
            target = archive / f"{slug}-{date.today().isoformat()}"
        shutil.move(str(d), str(target))
        print(f"  Archived: {d} -> {target}")

    def list(self, ctx: dict) -> list[PlatformRef]:
        posts_dir = self._posts_dir(ctx)
        refs = []
        for d in sorted(posts_dir.iterdir()):
            if not d.is_dir():
                continue
            md = d / f"{d.name}.md"
            if md.exists():
                refs.append(PlatformRef(platform="repo", post_id=d.name,
                                        url=f"https://archerships.com/essays/{d.name}.html"))
        return refs


def _register() -> None:
    from ..registry import register
    register(RepoPlugin)


_register()
