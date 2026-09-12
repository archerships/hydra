#!/Users/crasch/av/venv/hydra/bin/python3
"""
substack-assign-section.py
Uses Playwright to assign an existing Substack post to a specific section.
Usage: ./substack-assign-section.py <post_url_or_slug> <section_name>
"""

import os
import sys
import time
import keyring
from playwright.sync_api import sync_playwright
from substack_api.post import Post

# Configuration
PUBLICATION_NAME = "archerships" 
SUBSTACK_URL = "https://substack.com/sign-in"

def get_credentials():
    email = keyring.get_password('substack', 'email')
    password = keyring.get_password('substack', 'password')
    
    if not email or not password:
        print("Error: Substack credentials not found in system keyring.")
        sys.exit(1)
    return email, password

def login(page, email):
    print(f"Logging in to Substack as {email}...")
    page.goto(SUBSTACK_URL)
    page.fill('input[name="email"]', email)
    page.click('button:has-text("Confirm")')
    print("Please handle any Magic Link or 2FA in the browser window...")
    try:
        page.wait_for_url("**/dashboard**", timeout=120000)
        print("Login successful.")
    except:
        print("Login timed out or failed.")
        sys.exit(1)

def assign_section(page, post_id, section_name):
    edit_url = f"https://{PUBLICATION_NAME}.substack.com/publish/post/{post_id}"
    print(f"Navigating to edit page: {edit_url}")
    page.goto(edit_url)
    
    # 1. Open Settings
    print("Opening Settings menu...")
    page.wait_for_selector('button:has-text("Settings")', timeout=30000)
    page.click('button:has-text("Settings")')
    
    # 2. Find the Section dropdown
    # Substack settings uses a side panel or modal. 
    # We look for a label "Section" and the associated select/dropdown.
    print(f"Attempting to set section to: {section_name}")
    
    # Wait for the settings panel to appear
    page.wait_for_selector('div:has-text("Section")', timeout=10000)
    
    # Click the dropdown (usually follows the "Section" label)
    # This part is highly dependent on Substack's current DOM.
    # We use a broad search for the text.
    dropdown = page.locator('div.pencraft:has-text("Section")').locator('select, .select-container, button').first
    dropdown.click()
    
    # 3. Select the option
    # Options are usually in a list or native select
    try:
        page.click(f'text="{section_name}"')
    except:
        # Fallback for native select
        page.select_option('select', label=section_name)

    # 4. Save/Continue
    print("Saving changes...")
    page.click('button:has-text("Done")')
    
    # Final Save in the main editor
    page.wait_for_selector('button:has-text("Save")', timeout=10000)
    page.click('button:has-text("Save")')
    
    print(f"Successfully assigned post {post_id} to {section_name}")

def main():
    if len(sys.argv) < 3:
        print("Usage: ./substack-assign-section.py <url_or_slug> <section_name>")
        sys.exit(1)

    target = sys.argv[1]
    section_name = sys.argv[2]
    
    # Resolve post ID using the API library
    if "archerships.substack.com" not in target:
        target = f"https://{PUBLICATION_NAME}.substack.com/p/{target}"
    
    print(f"Resolving metadata for {target}...")
    try:
        p = Post(target)
        meta = p.get_metadata()
        post_id = meta.get("id")
        if not post_id:
            raise ValueError("Could not find post ID in metadata.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    email, _ = get_credentials()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        login(page, email)
        assign_section(page, post_id, section_name)
        
        print("Waiting 5 seconds before closing...")
        time.sleep(5)
        browser.close()

if __name__ == "__main__":
    main()
