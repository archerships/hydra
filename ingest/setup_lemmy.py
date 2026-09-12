import asyncio
from playwright.async_api import async_playwright
import sys

URL = "http://localhost:8080"

async def setup_lemmy():
    async with async_playwright() as p:
        print(f"INFO: Launching browser for {URL}...")
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            print("INFO: Loading setup page...")
            await page.goto(f"{URL}/setup", wait_until="networkidle")
            
            # 1. Initial Site Setup
            print("INFO: Performing site setup...")
            await page.fill('input[id="name"]', "Hydra Nexus")
            await page.fill('input[id="username"]', "admin")
            await page.fill('input[id="password"]', "admin_password")
            await page.fill('input[id="password_verify"]', "admin_password")
            await page.fill('input[id="email"]', "admin@localhost.local")
            
            await page.click('button[type="submit"]')
            await asyncio.sleep(5)
            
            # 2. Create Community
            print("INFO: Creating community 'archershipstesting'...")
            await page.goto(f"{URL}/create_community")
            await page.fill('input[id="name"]', "archershipstesting")
            await page.fill('input[id="display_name"]', "Archer Ships Testing")
            await page.click('button[type="submit"]')
            await asyncio.sleep(3)
            
            print("SUCCESS: Lemmy setup complete.")
            return True
            
        except Exception as e:
            print(f"CRITICAL: Setup failed: {e}")
            await page.screenshot(path="prj/hydra-nexus/setup_error.png")
            return False
        finally:
            await browser.close()

if __name__ == "__main__":
    success = asyncio.run(setup_lemmy())
    if not success:
        sys.exit(1)
