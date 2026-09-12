#!/usr/bin/env python3
"""
merge-twitter-bookmarks.py

Parse the Netscape Bookmark HTML produced by export-twitter-lists-to-bookmarks.py
and merge all list folders into the Twitter > lists folder in Brave's Bookmarks JSON.

Existing subfolders (seasteading, ai, guns) are preserved.
Duplicate list names are skipped (idempotent).

Usage:
  merge-twitter-bookmarks.py [--html PATH] [--bookmarks PATH] [--dry-run]
"""

import argparse
import json
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

HTML_PATH       = Path.home() / "av" / "doc" / "twitter_lists_bookmarks.html"
BOOKMARKS_PATH  = Path.home() / "Library" / "Application Support" / \
                  "BraveSoftware" / "Brave-Browser" / "Default" / "Bookmarks"
LISTS_GUID      = "ce4073bb-9e90-44e9-b7ab-13d15b444eb4"


# ---------------------------------------------------------------------------
# Windows FILETIME helpers
# ---------------------------------------------------------------------------
EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

def now_filetime() -> int:
    delta = datetime.now(timezone.utc) - EPOCH
    return int(delta.total_seconds() * 1_000_000)


# ---------------------------------------------------------------------------
# Netscape Bookmark HTML parser
# ---------------------------------------------------------------------------
class BookmarkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lists: dict[str, list[tuple[str, str]]] = {}  # name -> [(url, title), ...]
        self._current_list: str | None = None
        self._in_h3 = False
        self._in_a  = False
        self._cur_url   = ""
        self._cur_title = ""
        self._h3_depth  = 0      # track DL nesting to know which H3 is a list folder
        self._dl_stack  = []     # stack of context tags

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "dl":
            self._dl_stack.append("dl")
        elif tag == "h3":
            self._in_h3 = True
        elif tag == "a":
            self._in_a  = True
            self._cur_url   = attrs.get("href", "")
            self._cur_title = ""

    def handle_endtag(self, tag):
        if tag == "dl":
            if self._dl_stack:
                self._dl_stack.pop()
            if len(self._dl_stack) < 3:   # exited the list-level DL
                self._current_list = None
        elif tag == "h3":
            self._in_h3 = False
        elif tag == "a":
            self._in_a = False
            if self._current_list is not None and self._cur_url:
                self.lists[self._current_list].append(
                    (self._cur_url, self._cur_title.strip())
                )

    def handle_data(self, data):
        if self._in_h3 and len(self._dl_stack) >= 2:
            # H3 at DL depth 3+ is a list folder header
            if len(self._dl_stack) >= 2:
                name = data.strip()
                if name and name not in ("Twitter", "lists"):
                    self._current_list = name
                    if name not in self.lists:
                        self.lists[name] = []
        elif self._in_a:
            self._cur_title += data


def parse_html(path: Path) -> dict[str, list[tuple[str, str]]]:
    parser = BookmarkParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.lists


# ---------------------------------------------------------------------------
# Bookmarks JSON helpers
# ---------------------------------------------------------------------------
def find_node_by_guid(node: dict, guid: str) -> dict | None:
    if node.get("guid") == guid:
        return node
    for child in node.get("children", []):
        result = find_node_by_guid(child, guid)
        if result:
            return result
    return None


def make_folder(name: str, next_id: list) -> dict:
    nid = next_id[0]
    next_id[0] += 1
    ts = str(now_filetime())
    return {
        "children": [],
        "date_added": ts,
        "date_last_used": "0",
        "date_modified": ts,
        "guid": str(uuid.uuid4()),
        "id": str(nid),
        "name": name,
        "source": "user_add",
        "type": "folder",
    }


def make_url(title: str, url: str, next_id: list) -> dict:
    nid = next_id[0]
    next_id[0] += 1
    ts = str(now_filetime())
    return {
        "date_added": ts,
        "date_last_used": "0",
        "guid": str(uuid.uuid4()),
        "id": str(nid),
        "name": title,
        "source": "user_add",
        "type": "url",
        "url": url,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html",      type=Path, default=HTML_PATH)
    ap.add_argument("--bookmarks", type=Path, default=BOOKMARKS_PATH)
    ap.add_argument("--dry-run",   action="store_true")
    args = ap.parse_args()

    if not args.html.exists():
        sys.exit(f"[ERROR] HTML file not found: {args.html}")
    if not args.bookmarks.exists():
        sys.exit(f"[ERROR] Bookmarks file not found: {args.bookmarks}")

    print(f"[INFO] Parsing {args.html} ...")
    lists = parse_html(args.html)
    print(f"[INFO] Found {len(lists)} lists, "
          f"{sum(len(v) for v in lists.values())} total members")

    with open(args.bookmarks, encoding="utf-8") as f:
        data = json.load(f)

    lists_folder = find_node_by_guid(
        {"children": list(data["roots"].values())}, LISTS_GUID
    )
    if not lists_folder:
        sys.exit(f"[ERROR] Could not find lists folder (guid={LISTS_GUID})")

    existing_names = {c["name"] for c in lists_folder.get("children", [])}
    print(f"[INFO] Existing subfolders: {sorted(existing_names)}")

    # Compute next available ID
    def max_id(node):
        m = int(node.get("id", 0))
        for c in node.get("children", []):
            m = max(m, max_id(c))
        return m
    next_id = [max(max_id(r) for r in data["roots"].values()
                   if isinstance(r, dict)) + 1]

    added_folders = 0
    skipped = 0
    for list_name, members in lists.items():
        if list_name in existing_names:
            print(f"  [SKIP] {list_name!r} already exists")
            skipped += 1
            continue
        folder = make_folder(list_name, next_id)
        for url, title in members:
            folder["children"].append(make_url(title or url, url, next_id))
        lists_folder["children"].append(folder)
        added_folders += 1
        print(f"  [ADD]  {list_name!r} ({len(members)} members)")

    if args.dry_run:
        print(f"\n[DRY RUN] Would add {added_folders} folders, skip {skipped}. No changes written.")
        return

    # Back up original
    backup = args.bookmarks.with_suffix(".bak")
    shutil.copy2(args.bookmarks, backup)
    print(f"\n[INFO] Backup written to {backup}")

    with open(args.bookmarks, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=3)

    print(f"[SUCCESS] Added {added_folders} list folders ({skipped} skipped). "
          f"Restart Brave to see changes.")


if __name__ == "__main__":
    main()
