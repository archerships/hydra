import json
import requests
import os
import time
import subprocess
import sys
import asyncio

# Configuration
LEMMY_API = "http://localhost:8080/api/v3"
PEERTUBE_API = "http://peertube.localhost:8082/api/v1"
TEST_VIDEO = "/Users/crasch/av/ast/vid/Shockfactor_AI/46K_views_9.5K_reactions_They_were_starting_with_the_basics._explainedagain_undeadmanagement_slowprogress_Shockfactor_AI883627497450654.mp4"

# Add the src directory to sys.path so we can import NexusDaemon
sys.path.append(os.path.join(os.getcwd(), "prj/hydra-nexus/src"))
from sync_daemon import NexusDaemon

def mock_signal_envelope(text, video_path):
    return {
        "sourceName": "Robust Integration Bot",
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

async def run_robust_test():
    ts = int(time.time())
    test_msg = f"Robust Pipeline Test {ts}"
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
    
    print("DEBUG: Manually triggering process_envelope (includes wait-for-transcode)...")
    daemon.process_envelope(envelope)

    # 2. Verify link in Lemmy post
    print("STEP 2: Polling Lemmy for the post and verifying link reachability...")
    success = False
    for _ in range(15):
        try:
            resp = requests.get(f"{LEMMY_API}/post/list", params={"community_name": "archershipstesting", "sort": "New", "limit": 5}, timeout=5)
            posts = resp.json().get('posts', [])
            for p in posts:
                if test_msg in p['post']['name']:
                    video_url = p['post'].get('url', '')
                    print(f"FOUND: Post found with URL: {video_url}")
                    
                    # Verify the URL returns 200 (not 404)
                    print(f"INFO: Testing link reachability...")
                    head_resp = requests.head(video_url, timeout=10)
                    # PeerTube might return 405 for HEAD, so try GET with range
                    if head_resp.status_code != 200:
                        get_resp = requests.get(video_url, headers={"Range": "bytes=0-1"}, timeout=10)
                        if get_resp.status_code == 206 or get_resp.status_code == 200:
                            print(f"SUCCESS: Video link is functional (HTTP {get_resp.status_code}).")
                            success = True
                            break
                    else:
                        print(f"SUCCESS: Video link is functional (HTTP 200).")
                        success = True
                        break
            if success: break
        except Exception as e:
            print(f"DEBUG: Verification error: {e}")
        time.sleep(5)

    # Cleanup
    if os.path.exists(target_path): os.remove(target_path)
    
    if not success:
        print("FAILURE: Video link was either missing or resulted in a 404.")
        return False
        
    print("\nOVERALL STATUS: PASSED")
    return True

if __name__ == "__main__":
    result = asyncio.run(run_robust_test())
    if not result:
        sys.exit(1)
