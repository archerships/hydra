import json
import requests
import os
import time
import socket
import subprocess

# Configuration
LEMMY_API = "http://localhost:8080/api/v3"
COMMUNITY_NAME = "archershipstesting"
SOCKET_PATH = "/tmp/signal-cli.sock"
TEST_IMAGE = "/tmp/nexus_test_image.png"

def create_test_image():
    # Use ImageMagick to create a small solid colored square
    subprocess.run(["magick", "-size", "100x100", "xc:blue", TEST_IMAGE], check=True)
    return TEST_IMAGE

def send_signal_message(text, image_path):
    # Construct the JSON-RPC send command
    payload = {
        "jsonrpc": "2.0",
        "method": "send",
        "params": {
            "message": text,
            "groupId": "G9qiBfTE6f98O+KiyWdYVNdARUKB31x45DEvBC8gHDA=",
            "attachments": [image_path]
        },
        "id": "test_post"
    }
    
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(SOCKET_PATH)
        client.sendall(json.dumps(payload).encode() + b"\n")
        resp = client.recv(4096)
        print(f"DEBUG: Signal Response: {resp.decode()}", flush=True)

def verify_lemmy_post(expected_text):
    print(f"INFO: Waiting for Lemmy post synchronization...", flush=True)
    for _ in range(30): # Wait up to 30 seconds
        try:
            resp = requests.get(f"{LEMMY_API}/post/list", params={"community_name": COMMUNITY_NAME, "sort": "New", "limit": 1}, timeout=5)
            if resp.status_code == 200:
                posts = resp.json().get('posts', [])
                if posts:
                    latest = posts[0]['post']
                    if expected_text in latest['name'] or expected_text in latest.get('body', ''):
                        image_url = latest.get('url')
                        print(f"FOUND: Post '{latest['name']}' with URL: {image_url}", flush=True)
                        if image_url and "pictrs" in image_url:
                            return True, "SUCCESS: Text and image found."
                        else:
                            return False, f"FAILURE: Text found, but image URL missing or invalid: {image_url}"
        except Exception as e:
            print(f"DEBUG: Lemmy check error: {e}", flush=True)
        time.sleep(1)
    return False, "FAILURE: Post not found within timeout."

if __name__ == "__main__":
    ts = int(time.time())
    test_msg = f"Integration Test {ts}"
    img_path = create_test_image()
    
    print(f"STEP 1: Sending Signal message: {test_msg}", flush=True)
    send_signal_message(test_msg, img_path)
    
    print(f"STEP 2: Verifying in Lemmy...", flush=True)
    success, message = verify_lemmy_post(test_msg)
    
    print(message)
    if not success:
        exit(1)
