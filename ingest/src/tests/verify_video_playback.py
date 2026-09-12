import asyncio
import requests
from playwright.async_api import async_playwright
import os

LEMMY_UI = "http://localhost:8080"
COMMUNITY = "archershipstesting"

async def verify_playback():
    # 1. Get the latest post with a video URL
    print(f"INFO: Fetching latest video post from {COMMUNITY}...")
    resp = requests.get(f"{LEMMY_UI}/api/v3/post/list", params={"community_name": COMMUNITY, "sort": "New", "limit": 10})
    posts = resp.json().get('posts', [])
    
    video_post = None
    for p in posts:
        url = p['post'].get('url', '')
        if url and (url.endswith('.webm') or url.endswith('.mp4')):
            video_post = p
            break
            
    if not video_post:
        print("FAILURE: No video posts found in the last 10 submissions.")
        return False

    post_id = video_post['post']['id']
    video_url = video_post['post']['url']
    print(f"FOUND: Post ID {post_id} with video: {video_url}")

    # 2. Start Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Load the specific post page
        target_url = f"{LEMMY_UI}/post/{post_id}"
        print(f"INFO: Loading {target_url}...")
        await page.goto(target_url, wait_until="networkidle")
        
        # 3. Check for <video> element
        video_selector = "video"
        try:
            await page.wait_for_selector(video_selector, timeout=10000)
            print("SUCCESS: <video> element found in UI.")
        except:
            print("FAILURE: <video> element not found in UI within 10s.")
            # Debug: what elements ARE on the page?
            content = await page.content()
            print(f"DEBUG: Page has {len(content)} bytes of HTML.")
            # Search for the video URL in the HTML
            if video_url in content:
                print("DEBUG: Video URL found in HTML source.")
                tag_name = await page.evaluate(f"document.querySelector('[href*=\"{video_url}\"], [src*=\"{video_url}\"]').tagName")
                print(f"DEBUG: Element tag name: {tag_name}")
            else:
                print("DEBUG: Video URL NOT found in HTML source.")
            
            await page.screenshot(path="prj/hydra-nexus/tests/video_failure.png")
            await browser.close()
            return False

        # 4. Verify Playability (Ready State)
        # 0 = HAVE_NOTHING
        # 1 = HAVE_METADATA
        # 2 = HAVE_CURRENT_DATA
        # 3 = HAVE_FUTURE_DATA
        # 4 = HAVE_ENOUGH_DATA
        ready_state = await page.evaluate("document.querySelector('video').readyState")
        print(f"INFO: Video readyState: {ready_state}")
        
        if ready_state >= 2:
            print("SUCCESS: Video is loaded and playable (readyState >= 2).")
            await page.screenshot(path="prj/hydra-nexus/tests/video_success.png")
            await browser.close()
            return True
        else:
            # Wait a bit more for metadata
            print("INFO: Waiting for video metadata...")
            await asyncio.sleep(5)
            ready_state = await page.evaluate("document.querySelector('video').readyState")
            print(f"INFO: Final video readyState: {ready_state}")
            
            await page.screenshot(path="prj/hydra-nexus/tests/video_final_state.png")
            await browser.close()
            return ready_state >= 2

if __name__ == "__main__":
    success = asyncio.run(verify_playback())
    if not success:
        exit(1)
