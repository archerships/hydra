import json
import requests
import os
import time
import subprocess
import sys
import asyncio
from playwright.async_api import async_playwright

# Configuration
LEMMY_API = "http://localhost:8080/api/v3"
PEERTUBE_API = "http://peertube.localhost:8082/api/v1"
TEST_VIDEO = "/Users/crasch/av/ast/vid/Shockfactor_AI/46K_views_9.5K_reactions_They_were_starting_with_the_basics._explainedagain_undeadmanagement_slowprogress_Shockfactor_AI883627497450654.mp4"

# Add the src directory to sys.path so we can import NexusDaemon
sys.path.append(os.path.join(os.getcwd(), "prj/hydra-nexus/src"))
from sync_daemon import NexusDaemon

def mock_signal_envelope(text, video_path):
    return {
        "sourceName": "Integration Bot",
        "dataMessage": {
            "message": text,
            "groupInfo": {
                "groupId": "G9qiBfTE6f98O+KiyWdYVNdARUKB31x45DEvBC8gHDA="
            },
            "attachments": [
                {
                    "contentType": "video/mp4",
                    "id": os.path.basename(video_path),
                    "filename": os.path.basename(video_path)
                }
            ]
        }
    }

async def run_full_test():
    ts = int(time.time())
    test_msg = f"Mock Pipeline Integration Test {ts}"
    print(f"INFO: Starting test with message: {test_msg}")

    # Initialize the actual daemon class to use its logic
    print("INFO: Initializing NexusDaemon for pipeline test...")
    daemon = NexusDaemon()

    # 1. Mock Signal Event
    print("STEP 1: Injecting mock Signal envelope...")
    envelope = mock_signal_envelope(test_msg, TEST_VIDEO)
    
    # Symlink file so daemon can find it in ATTACHMENT_DIR
    target_path = os.path.join(os.path.expanduser("~/.local/share/signal-cli/attachments/"), os.path.basename(TEST_VIDEO))
    if os.path.exists(target_path): os.remove(target_path)
    os.symlink(TEST_VIDEO, target_path)
    
    print("DEBUG: Manually triggering process_envelope (this includes PeerTube upload)...")
    daemon.process_envelope(envelope)

    # 2. Poll PeerTube for the video
    print("STEP 2: Polling PeerTube for upload (timeout 120s)...")
    video_uuid = None
    for _ in range(24): # 120 seconds
        try:
            resp = requests.get(f"{PEERTUBE_API}/video-channels/signal_archive/videos", timeout=5)
            if resp.status_code == 200:
                videos = resp.json().get('data', [])
                for v in videos:
                    if test_msg in v['name']:
                        video_uuid = v['uuid']
                        print(f"SUCCESS: Video found in PeerTube: {video_uuid}")
                        break
            if video_uuid: break
        except Exception as e:
            print(f"DEBUG: PeerTube poll error: {e}")
        time.sleep(5)
    
    if not video_uuid:
        print("FAILURE: Video did not appear in PeerTube.")
        return False

    # 3. Poll Lemmy for the post
    print("STEP 3: Polling Lemmy for post and Thumbnail metadata (timeout 30s)...")
    lemmy_post_id = None
    for _ in range(15):
        try:
            resp = requests.get(f"{LEMMY_API}/post/list", params={"community_name": "archershipstesting", "sort": "New", "limit": 5}, timeout=5)
            posts = resp.json().get('posts', [])
            for p in posts:
                if test_msg in p['post']['name']:
                    # CHECK FOR THUMBNAIL (Converted from custom_thumbnail by Lemmy backend)
                    thumb_url = p['post'].get('thumbnail_url')
                    if thumb_url and "pictrs" in thumb_url:
                        lemmy_post_id = p['post']['id']
                        print(f"SUCCESS: Post found with local Pictrs thumbnail: {thumb_url}")
                        break
            if lemmy_post_id: break
        except Exception as e:
            print(f"DEBUG: Lemmy poll error: {e}")
        time.sleep(2)

    if not lemmy_post_id:
        print("FAILURE: Post not found or thumbnail fetch failed.")
        return False

    # 4. Verify UI and Playback via Playwright
    print("STEP 4: Verifying UI via Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        target_url = f"http://localhost:8080/post/{lemmy_post_id}"
        print(f"INFO: Loading {target_url}...")
        await page.goto(target_url, wait_until="networkidle")
        
        content = await page.content()
        # Look for the embed URL in the rendered HTML
        if 'embed' in content.lower():
            print("SUCCESS: Video embed found in Lemmy UI.")
        else:
            print("FAILURE: Video embed NOT found in Lemmy UI HTML.")
            await browser.close()
            return False

        # PeerTube usually embeds as a link in our current body format
        link_selector = f'a[href*="{video_uuid}"]'
        try:
            await page.wait_for_selector(link_selector, timeout=5000)
            print("SUCCESS: PeerTube link is a clickable element.")
        except:
            print("FAILURE: PeerTube link element not interactable.")
            await browser.close()
            return False

        await page.screenshot(path="prj/hydra-nexus/tests/full_pipeline_success.png")
        await browser.close()
        
    print("\nOVERALL STATUS: PASSED")
    # Cleanup
    if os.path.exists(target_path): os.remove(target_path)
    return True

if __name__ == "__main__":
    success = asyncio.run(run_full_test())
    if not success:
        sys.exit(1)
