import asyncio
from playwright.async_api import async_playwright
import sys

URL = "http://localhost:8080"
USER = "admin"
PASS = "admin_password"

async def check_purge_button():
    async with async_playwright() as p:
        print(f"INFO: Launching browser for {URL}...")
        browser = await p.chromium.launch(headless=True)
        # Use a fresh context to avoid any old session data
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # 1. Login
            print("INFO: Logging in...")
            await page.goto(f"{URL}/login", wait_until="networkidle")
            await page.fill('input[name="username"]', USER)
            await page.fill('input[name="password"]', PASS)
            await page.click('button[type="submit"]')
            await page.wait_for_url(f"{URL}/", timeout=10000)
            print("SUCCESS: Logged in.")

            # 2. Navigate to community
            print("INFO: Navigating to community...")
            await page.goto(f"{URL}/c/archershipstesting", wait_until="networkidle")
            await asyncio.sleep(3) # Give Angular time to render posts

            # 3. Open moderation menu
            print("INFO: Opening post menu...")
            # Click the first '...' button found
            menu_button = page.locator('button:has(svg)').nth(2) # Fallback if specific ID not found
            # Better: look for the vertical ellipsis or 'More'
            try:
                await page.click('button[aria-label="More"]', timeout=5000)
            except:
                # Click the specific dropdown button for the first post
                await page.locator('.dropdown-toggle').first.click()
            
            await asyncio.sleep(1) # Wait for menu to open
            
            # 4. Check for Purge
            content = await page.content()
            print(f"DEBUG: Menu opened. HTML length: {len(content)}")
            
            await page.screenshot(path="prj/hydra-nexus/purge_check.png")
            
            if "purge" in content.lower():
                print("SUCCESS: 'Purge' option found in the UI!")
                return True
            else:
                print("FAILURE: 'Purge' option NOT found in the UI.")
                # Print the menu items we DO see
                menu_items = await page.inner_text('.dropdown-menu') if await page.query_selector('.dropdown-menu') else "Menu container not found"
                print(f"DEBUG: Visible menu items: {menu_items}")
                return False
                
        except Exception as e:
            print(f"CRITICAL: Test error: {e}")
            await page.screenshot(path="prj/hydra-nexus/purge_error.png")
            return False
        finally:
            await browser.close()

if __name__ == "__main__":
    success = asyncio.run(check_purge_button())
    if not success:
        sys.exit(1)
