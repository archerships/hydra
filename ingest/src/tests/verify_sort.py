import asyncio
from playwright.async_api import async_playwright

async def verify_post_visibility():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print("--- Navigating to archershipstesting (Sort: New) ---")
            # Navigate directly with sort param
            await page.goto("http://localhost:8080/c/archershipstesting?sort=New", wait_until="networkidle")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="prj/hydra-nexus/tests/screenshot_community_new.png")
            
            posts = await page.query_selector_all(".post-listing")
            if posts:
                print(f"SUCCESS: Found {len(posts)} posts with 'New' sort.")
                for i, post in enumerate(posts):
                    text = await post.inner_text()
                    print(f"Post {i+1}: {text.strip()[:100]}...")
            else:
                print("FAILURE: No posts visible even with 'New' sort.")
                
        except Exception as e:
            print(f"EXCEPTION: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(verify_post_visibility())
