import asyncio
from playwright.async_api import async_playwright

async def check_communities():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print("--- Checking archershipstesting community ---")
            await page.goto("http://localhost:8080/c/archershipstesting", wait_until="networkidle")
            await page.screenshot(path="prj/hydra-nexus/tests/screenshot_community.png")
            
            # Check for posts
            posts = await page.query_selector_all(".post-listing")
            if posts:
                print(f"FOUND {len(posts)} posts in community.")
                for i, post in enumerate(posts[:5]):
                    text = await post.inner_text()
                    print(f"Post {i+1}: {text.strip()[:100]}...")
            else:
                print("No posts found in community.")
                
        except Exception as e:
            print(f"EXCEPTION: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(check_communities())
