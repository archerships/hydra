import json
import requests
import os
import time
import subprocess
import sys

# Configuration
LEMMY_API = "http://localhost:8080/api/v3"
COMMUNITY_NAME = "archershipstesting"
TEST_VIDEO = "/tmp/nexus_test_video.mp4"

# Add the src directory to sys.path so we can import NexusDaemon
sys.path.append(os.path.join(os.getcwd(), "prj/hydra-nexus/src"))
from sync_daemon import NexusDaemon

def create_test_video():
    # Use ImageMagick/ffmpeg to create a 2-second video
    print("INFO: Creating test video...", flush=True)
    for i in range(10):
        subprocess.run(["magick", "-size", "100x100", f"xc:hsb({i*36},100%,100%)", f"/tmp/frame_{i}.png"], check=True)
    
    subprocess.run(["ffmpeg", "-y", "-framerate", "5", "-i", "/tmp/frame_%d.png", "-c:v", "libx264", "-pix_fmt", "yuv420p", TEST_VIDEO], check=True, stderr=subprocess.DEVNULL)
    return TEST_VIDEO

def mock_signal_envelope(text, video_path):
    # This matches the structure expected by process_envelope
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

def verify_lemmy_post(expected_text):
    print(f"INFO: Verifying Lemmy post for '{expected_text}'...", flush=True)
    try:
        resp = requests.get(f"{LEMMY_API}/post/list", params={"community_name": COMMUNITY_NAME, "sort": "New", "limit": 1}, timeout=5)
        if resp.status_code == 200:
            posts = resp.json().get('posts', [])
            if posts:
                latest = posts[0]['post']
                if expected_text in latest['name']:
                    image_url = latest.get('url')
                    body = latest.get('body', '')
                    print(f"FOUND: Post '{latest['name']}' with URL: {image_url} and Body: {body[:50]}...", flush=True)
                    if "peertube.localhost" in body:
                        return True, "SUCCESS: Text and PeerTube watch link found in Lemmy."
                    else:
                        return False, f"FAILURE: PeerTube link missing from body: {body}"
    except Exception as e:
        return False, f"FAILURE: API error: {e}"
    return False, "FAILURE: Post not found."

if __name__ == "__main__":
    ts = int(time.time())
    test_msg = f"Pipeline Test {ts}"
    vid_path = create_test_video()
    
    # Initialize the actual daemon class to use its logic
    print("INFO: Initializing NexusDaemon for pipeline test...", flush=True)
    daemon = NexusDaemon()
    
    # Manually trigger processing
    print(f"STEP 1: Injecting mock Signal envelope for: {test_msg}", flush=True)
    envelope = mock_signal_envelope(test_msg, vid_path)
    
    # We must ensure the file exists in ATTACHMENT_DIR for the daemon to find it
    # We'll symlink it there for the test
    target_path = os.path.join(os.path.expanduser("~/.local/share/signal-cli/attachments/"), os.path.basename(vid_path))
    if os.path.exists(target_path): os.remove(target_path)
    os.symlink(vid_path, target_path)
    
    daemon.process_envelope(envelope)
    
    print(f"STEP 2: Verifying in Lemmy...", flush=True)
    time.sleep(2) # Give Pictrs/Lemmy a moment
    success, message = verify_lemmy_post(test_msg)
    
    print(message)
    # Cleanup
    if os.path.exists(target_path): os.remove(target_path)
    
    if not success:
        exit(1)
