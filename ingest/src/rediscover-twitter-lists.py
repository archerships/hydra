#!/usr/bin/env python3
"""
rediscover-twitter-lists.py

Re-run list URL discovery with a higher stale threshold to catch all lists.
Merges newly found lists into the existing JSON (skips already-known ones).
"""
import asyncio
import json
import re
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    sys.exit("playwright not installed: pip install playwright && playwright install chromium")

CDP_URL    = "http://localhost:9222"
LISTS_JSON = Path.home() / "av" / "doc" / "twitter_lists.json"
HANDLE     = "archerships"
STALE_MAX  = 8   # wait for 8 consecutive no-new-cells checks before stopping


async def harvest_visible_names(page) -> dict[str, str]:
    """Collect {name: href} for all listCell elements currently in the DOM.
    Returns name -> partial href (e.g. '/i/lists/12345') or '' if not found.
    """
    result = {}
    cells = await page.query_selector_all('[data-testid="listCell"]')
    for cell in cells:
        name_el = await cell.query_selector('[dir="ltr"]')
        if not name_el:
            continue
        name = (await name_el.inner_text()).split('\n')[0].strip()
        if not name:
            continue
        # Try to read href from any <a> inside the cell
        a = await cell.query_selector('a[href*="/i/lists/"]')
        href = (await a.get_attribute('href')) if a else ''
        result[name] = href
    return result


async def scroll_and_harvest(page) -> dict[str, str]:
    """Scroll the page, harvesting list names incrementally.
    Stops after STALE_MAX consecutive scrolls with no new names.
    Returns {name: href_or_empty}.
    """
    seen: dict[str, str] = {}
    stale = 0
    scroll_num = 0
    while stale < STALE_MAX:
        visible = await harvest_visible_names(page)
        new = {k: v for k, v in visible.items() if k not in seen}
        seen.update(visible)
        if new:
            stale = 0
            print(f"  [scroll {scroll_num}] +{len(new)} new names, total={len(seen)}: "
                  f"{list(new)[:3]}{'...' if len(new)>3 else ''}", flush=True)
        else:
            stale += 1
            print(f"  [scroll {scroll_num}] no new names, stale={stale}/{STALE_MAX} "
                  f"(total={len(seen)})", flush=True)
        await page.evaluate("window.scrollBy(0, 800)")
        await asyncio.sleep(1.5)
        scroll_num += 1
    return seen


async def main():
    existing = json.loads(LISTS_JSON.read_text()) if LISTS_JSON.exists() else {}
    print(f"[INFO] {len(existing)} lists already known. Re-running discovery...")

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        page = await context.new_page()

        print(f"[INFO] Loading https://x.com/{HANDLE}/lists ...")
        await page.goto(f"https://x.com/{HANDLE}/lists",
                        wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(5)

        try:
            await page.wait_for_selector('[data-testid="listCell"]', timeout=20000)
        except Exception:
            sys.exit("[FAILED] No listCell elements found — make sure you are logged in.")

        print("[INFO] Scrolling and harvesting list names incrementally...")
        all_names = await scroll_and_harvest(page)

        # Identify which names are new (not in existing JSON)
        new_names = [n for n in all_names if n not in existing]
        print(f"\n[INFO] {len(all_names)} total names seen, {len(new_names)} are new.")

        if not new_names:
            print("[INFO] Nothing new to capture.")
            await page.close()
            return

        # For new names, scroll back to top and click each to get URL
        print("[INFO] Scrolling back to top to click new list cells...")
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(2)

        lists = dict(existing)
        captured = 0

        # We need to find and click each new-named cell.
        # Since the DOM is virtual, we scroll until the target cell appears, click it.
        for target_name in new_names:
            print(f"  Seeking {target_name!r}...", end=" ", flush=True)
            found = False
            for attempt in range(60):  # scroll up to 60 times to find this cell
                cells = await page.query_selector_all('[data-testid="listCell"]')
                for cell in cells:
                    name_el = await cell.query_selector('[dir="ltr"]')
                    if not name_el:
                        continue
                    name = (await name_el.inner_text()).split('\n')[0].strip()
                    if name != target_name:
                        continue
                    # Found it — click
                    await page.evaluate("(el) => el.scrollIntoView({block:'center'})", cell)
                    await asyncio.sleep(0.4)
                    try:
                        async with page.expect_navigation(timeout=8000):
                            await cell.click()
                    except Exception:
                        await asyncio.sleep(1)
                    m = re.search(r'/i/lists/(\d+)', page.url)
                    list_url = f"https://x.com/i/lists/{m.group(1)}" if m else page.url
                    print(f"→ {m.group(1) if m else page.url}")
                    lists[target_name] = list_url
                    captured += 1
                    found = True
                    await page.go_back(wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2)
                    break
                if found:
                    break
                await page.evaluate("window.scrollBy(0, 600)")
                await asyncio.sleep(1.0)
            if not found:
                print(f"[WARN] Could not find cell for {target_name!r}")

        await page.close()

    LISTS_JSON.write_text(json.dumps(lists, indent=2, ensure_ascii=False))
    print(f"\n[SUCCESS] {captured} new lists captured. Total: {len(lists)}")
    print(f"          Saved to: {LISTS_JSON}")


asyncio.run(main())
