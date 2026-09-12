#!/Users/crasch/av/venv/hydra/bin/python3
"""
essay_frontmatter.py -- Shared utility for reading/writing essay frontmatter.

Importable as a module from other scripts in publish/src/:
    from essay_frontmatter import load_fm, get_platform_entry, set_platform_field, find_md_for_html
"""

from __future__ import annotations  # py3.9 Makefile test compat (PEP 604)

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml not found: pip install pyyaml")

_FM_RE = re.compile(r'^---\n(.*?)\n---\n', re.DOTALL)


def load_fm(md_path: Path) -> dict:
    """Parse YAML frontmatter from a .md file. Returns {} if none."""
    text = md_path.read_text(encoding='utf-8')
    m = _FM_RE.match(text)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def get_platform_entry(fm: dict, platform: str) -> dict | None:
    """Return the published_at entry dict for a platform, or None."""
    for entry in fm.get('published_at') or []:
        if isinstance(entry, dict) and entry.get('platform') == platform:
            return entry
    return None


def set_platform_field(md_path: Path, platform: str, **fields) -> None:
    """
    Add or update fields in the published_at entry for a platform in the .md file.
    If no entry exists for the platform, appends one. Rewrites the file in-place.

    Example:
        set_platform_field(md, 'substack', id='199558904')
        set_platform_field(md, 'twitter', id='2059672670686646272',
                           url='https://x.com/archerships/status/...')
    """
    text = md_path.read_text(encoding='utf-8')
    m = _FM_RE.match(text)
    if not m:
        raise ValueError(f"No frontmatter found in {md_path}")

    fm = yaml.safe_load(m.group(1)) or {}
    published_at = list(fm.get('published_at') or [])

    found = False
    for entry in published_at:
        if isinstance(entry, dict) and entry.get('platform') == platform:
            entry.update(fields)
            found = True
            break
    if not found:
        entry = {'platform': platform}
        entry.update(fields)
        published_at.append(entry)

    fm['published_at'] = published_at
    new_fm = yaml.dump(fm, default_flow_style=False, allow_unicode=True,
                       sort_keys=False).rstrip('\n')
    md_path.write_text(f"---\n{new_fm}\n---\n" + text[m.end():], encoding='utf-8')


def find_md_for_html(html_path: Path) -> Path | None:
    """Given an essay HTML path, return the corresponding .md source or None."""
    stem = html_path.stem
    # Per-directory layout: ~/av/doc/posts/SLUG/SLUG.html -> SLUG/SLUG.md
    candidate = html_path.parent / f"{stem}.md"
    if candidate.exists():
        return candidate
    # Flat layout fallback
    candidate = html_path.with_suffix('.md')
    if candidate.exists():
        return candidate
    return None


# ---------------------------------------------------------------------------
# CRUD refs: get/set/remove a full platform ref (id, url, post_type,
# destination, account, timestamps) in published_at.
# ---------------------------------------------------------------------------

def get_platform_ref(md_path: Path, platform: str) -> dict | None:
    """Return the full published_at entry dict for a platform, or None."""
    return get_platform_entry(load_fm(md_path), platform)


def set_platform_ref(md_path: Path, platform: str, ref: dict) -> None:
    """Set (add or replace) the published_at entry for a platform.

    `ref` fields: url, id, post_type, destination, account, created_at,
    updated_at. The entry is updated in place; other fields preserved.
    """
    fields = {k: v for k, v in ref.items() if v not in (None, "")}
    set_platform_field(md_path, platform, **fields)


def remove_platform_entry(md_path: Path, platform: str) -> bool:
    """Remove the published_at entry for a platform. Returns True if removed."""
    text = md_path.read_text(encoding='utf-8')
    m = _FM_RE.match(text)
    if not m:
        raise ValueError(f"No frontmatter found in {md_path}")

    fm = yaml.safe_load(m.group(1)) or {}
    published_at = [e for e in (fm.get('published_at') or [])
                    if not (isinstance(e, dict) and e.get('platform') == platform)]
    if len(published_at) == len(fm.get('published_at') or []):
        return False

    fm['published_at'] = published_at
    new_fm = yaml.dump(fm, default_flow_style=False, allow_unicode=True,
                       sort_keys=False).rstrip('\n')
    md_path.write_text(f"---\n{new_fm}\n---\n" + text[m.end():], encoding='utf-8')
    return True
