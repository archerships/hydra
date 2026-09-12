import asyncio
from playwright.async_api import async_playwright
import sys

async def test_login():
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Monitor all network activity
        page.on("request", lambda request: print(f">> REQ: {request.method} {request.url}"))
        page.on("response", lambda response: print(f"<< RES: {response.status} {response.url}"))
        page.on("console", lambda msg: print(f"CONSOLE [{msg.type}]: {msg.text}"))
        page.on("requestfailed", lambda request: print(f"REQUEST FAILED: {request.url} ({request.failure.error_text if request.failure else 'N/A'})"))

        try:
            print("--- Navigating to Lemmy Login Page ---")
            await page.goto("http://localhost:8080/login", wait_until="networkidle", timeout=30000)
            
            # Manually inject the API URL into local storage/session if needed, 
            # though Lemmy-ui usually gets it from the page source.
            
            await page.screenshot(path="prj/hydra-nexus/tests/screenshot_login_before_fill.png")
            
            # The actual ID is login-email-or-username
            username_input = await page.query_selector('input[id="login-email-or-username"]')
            if username_input:
                print("Found username input on /login")
                await page.fill('input[id="login-email-or-username"]', 'admin')
                # Password field doesn't have a stable ID in the same way, let's use type="password"
                await page.fill('input[type="password"]', 'admin_password')
                # Target the login button specifically in the login form
                await page.click('form button[type="submit"]:has-text("Login")')
                
                print("Clicked submit, waiting for response...")
                await page.wait_for_timeout(5000)
                await page.screenshot(path="prj/hydra-nexus/tests/screenshot_after_login_direct.png")
                
                # Check page content for login success indicators
                content = await page.content()
                if "Logout" in content:
                    print("SUCCESS: Logged in!")
                else:
                    print("FAILURE: Not logged in. Check screenshots.")
                    # Check for any visible error messages
                    error = await page.query_selector('.alert-danger')
                    if error:
                        print(f"Error: {await error.inner_text()}")
            else:
                print("FAILED to find username input on /login.")

        except Exception as e:
            print(f"EXCEPTION: {str(e)}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(test_login())
