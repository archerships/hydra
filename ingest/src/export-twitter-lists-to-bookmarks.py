#!/usr/bin/env python3
"""
export-twitter-lists-to-bookmarks.py

Connects to an existing Brave browser via CDP, loads each list's /members page
(profile cards only — no tweets), and generates a Netscape Bookmark HTML file.

Usage:
  export-twitter-lists-to-bookmarks.py <handle> [--cdp URL] [--out PATH]
                                        [--lists-json PATH] [-h]

Options:
  handle            Twitter/X handle (without @)
  --cdp URL         CDP endpoint (default: http://localhost:9222)
  --out PATH        Output file (default: ~/av/doc/twitter_lists_bookmarks.html)
  --lists-json PATH Pre-captured list map JSON (skip URL discovery phase)
  -h, --help        Show this message and exit
"""

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    sys.exit("playwright not installed: pip install playwright && playwright install chromium")

CDP_URL  = "http://localhost:9222"
OUT_PATH = Path.home() / "av" / "doc" / "twitter_lists_bookmarks.html"

SKIP_USERNAMES = {
    'i', 'explore', 'notifications', 'messages',
    'home', 'settings', 'search',
}


# ---------------------------------------------------------------------------
# Phase 1: discover list URLs by clicking each listCell once
# ---------------------------------------------------------------------------

async def scroll_load_all(page):
    prev = 0
    stale = 0
    while stale < 3:
        n = len(await page.query_selector_all('[data-testid="listCell"]'))
        stale = stale + 1 if n == prev else 0
        prev = n
        await page.evaluate("window.scrollBy(0, 1200)")
        await asyncio.sleep(1.5)
    return prev


async def discover_lists(page, handle: str) -> dict[str, str]:
    """Return {list_name: list_url} by clicking each cell once and capturing the URL."""
    print(f"[INFO] Loading https://x.com/{handle}/lists ...")
    await page.goto(f"https://x.com/{handle}/lists",
                    wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(5)

    try:
        await page.wait_for_selector('[data-testid="listCell"]', timeout=20000)
    except Exception:
        sys.exit("[FAILED] No listCell elements found — make sure you are logged in to X.")

    print("[INFO] Scrolling to load all lists...")
    await scroll_load_all(page)
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(2)

    total = len(await page.query_selector_all('[data-testid="listCell"]'))
    print(f"[INFO] {total} lists found. Capturing URLs...\n")

    lists: dict[str, str] = {}

    for i in range(total):
        await page.wait_for_selector('[data-testid="listCell"]', timeout=15000)
        cells = await page.query_selector_all('[data-testid="listCell"]')
        if i >= len(cells):
            continue

        cell = cells[i]
        name_el = await cell.query_selector('[dir="ltr"]')
        name = (await name_el.inner_text()).split('\n')[0].strip() if name_el else f"List_{i}"

        await page.evaluate("(el) => el.scrollIntoView({block:'center'})", cell)
        await asyncio.sleep(0.4)

        try:
            async with page.expect_navigation(timeout=8000):
                await cell.click()
        except Exception:
            await asyncio.sleep(1)

        m = re.search(r'/i/lists/(\d+)', page.url)
        list_url = f"https://x.com/i/lists/{m.group(1)}" if m else page.url
        print(f"  [{i+1}/{total}] {name!r:50} → {m.group(1) if m else '???'}")
        lists[name] = list_url

        await page.go_back(wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

    return lists


# ---------------------------------------------------------------------------
# Phase 2: extract members from each list's /members page (profile cards only)
# ---------------------------------------------------------------------------

async def collect_user_cells(page) -> dict[str, str]:
    """Collect all visible UserCell elements from the current page."""
    members: dict[str, str] = {}
    cells = await page.query_selector_all('[data-testid="UserCell"]')
    for cell in cells:
        link = await cell.query_selector('a[href^="/"][role="link"]')
        if not link:
            continue
        href = await link.get_attribute("href") or ""
        username = href.strip("/")
        if "/" in username or username in SKIP_USERNAMES:
            continue
        if username not in members:
            name_el = await cell.query_selector('[data-testid="User-Name"]')
            display = (await name_el.inner_text()).split('\n')[0].strip() if name_el else username
            members[username] = display
    return members


async def extract_members(page, list_url: str, list_name: str) -> dict[str, str]:
    """Go directly to /members page (profile cards only, no tweets), collect all."""
    members_url = list_url.rstrip("/") + "/members"
    print(f"  → {members_url}", end=" ", flush=True)

    try:
        await page.goto(members_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"[SKIP: navigation failed: {e}]")
        return {}

    await asyncio.sleep(3)

    try:
        await page.wait_for_selector('[data-testid="UserCell"]', timeout=15000)
    except Exception:
        print("[SKIP: no members found]")
        return {}

    # Collect initial batch
    members = await collect_user_cells(page)
    prev = 0

    # Scroll gently until no new members appear (max 10 scrolls)
    for _ in range(10):
        if len(members) == prev:
            break
        prev = len(members)
        await page.evaluate("window.scrollBy(0, 1200)")
        await asyncio.sleep(random.uniform(1.5, 2.5))
        new = await collect_user_cells(page)
        members.update(new)

    print(f"[{len(members)} members]")
    return members


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_html(all_data: dict[str, dict]) -> str:
    lines = [
        '<!DOCTYPE NETSCAPE-Bookmark-file-1>',
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        '<DL><p>',
        '    <DT><H3>Twitter</H3>',
        '    <DL><p>',
        '        <DT><H3>lists</H3>',
        '        <DL><p>',
    ]
    for list_name, members in all_data.items():
        lines.append(f'            <DT><H3>{escape(list_name)}</H3>')
        lines.append('            <DL><p>')
        for username, display_name in members.items():
            lines.append(
                f'                <DT><A HREF="https://x.com/{username}">'
                f'{escape(display_name)} (@{username})</A>'
            )
        lines.append('            </DL><p>')
    lines += ['        </DL><p>', '    </DL><p>', '</DL><p>']
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(handle: str, cdp: str, out: Path, lists_json: Path | None):
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp)
        context = browser.contexts[0]
        page = await context.new_page()

        # Phase 1 — get list URLs
        if lists_json and lists_json.exists():
            list_map = json.loads(lists_json.read_text())
            print(f"[INFO] Loaded {len(list_map)} lists from {lists_json}")
        else:
            list_map = await discover_lists(page, handle)
            if not list_map:
                await page.close()
                return
            print(f"\n[INFO] Captured {len(list_map)} list URLs.")

        # Phase 2 — extract members from each /members page
        all_data: dict[str, dict] = {}
        for i, (list_name, list_url) in enumerate(list_map.items()):
            print(f"\n[{i+1}/{len(list_map)}]", end=" ")
            all_data[list_name] = await extract_members(page, list_url, list_name)

        await page.close()

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_html(all_data), encoding="utf-8")
        total_members = sum(len(v) for v in all_data.values())
        print(f"\n[SUCCESS] {len(all_data)} lists, {total_members} total member entries")
        print(f"          Saved to: {out}")


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    p.add_argument("handle",          nargs="?")
    p.add_argument("--cdp",           default=CDP_URL)
    p.add_argument("--out",           type=Path, default=OUT_PATH)
    p.add_argument("--lists-json",    type=Path, default=None)
    p.add_argument("-h", "--help",    action="store_true")
    args = p.parse_args()
    if args.help or not args.handle:
        print(__doc__, end="")
        sys.exit(0)
    return args


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args.handle, args.cdp, args.out, args.lists_json))
