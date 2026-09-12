import json
import requests
import os
import time
import sys
import asyncio

# Configuration
LEMMY_API = "http://localhost:8080/api/v3"
COMMUNITY_NAME = "archershipstesting"

# Add the src directory to sys.path
sys.path.append(os.path.join(os.getcwd(), "prj/hydra-nexus/src"))
from sync_daemon import NexusDaemon

def mock_envelope(text, timestamp, source_num="+16501112222", quote=None):
    env = {
        "sourceNumber": source_num,
        "sourceName": "Thread Bot",
        "dataMessage": {
            "message": text,
            "timestamp": timestamp,
            "groupInfo": {
                "groupId": "G9qiBfTE6f98O+KiyWdYVNdARUKB31x45DEvBC8gHDA="
            }
        }
    }
    if quote:
        env["dataMessage"]["quote"] = quote
    return env

async def run_threading_test():
    print("INFO: Starting Threading Integration Test...")
    daemon = NexusDaemon()
    ts_parent = int(time.time() * 1000)
    parent_text = f"Parent Message {ts_parent}"
    
    # 1. Inject Parent Message
    print("STEP 1: Injecting parent message...")
    parent_env = mock_envelope(parent_text, ts_parent)
    daemon.process_envelope(parent_env)
    
    # Wait for Lemmy
    time.sleep(2)
    
    # Verify Parent Post exists and get ID
    resp = requests.get(f"{LEMMY_API}/post/list", params={"community_name": COMMUNITY_NAME, "sort": "New", "limit": 1})
    posts = resp.json().get('posts', [])
    if not posts or parent_text not in posts[0]['post']['name']:
        print("FAILURE: Parent post not created.")
        return False
    
    parent_post_id = posts[0]['post']['id']
    print(f"SUCCESS: Parent post created with ID: {parent_post_id}")

    # 2. Inject Reply Message
    ts_reply = int(time.time() * 1000)
    reply_text = f"Reply Message {ts_reply}"
    quote = {
        "id": ts_parent,
        "authorNumber": "+16501112222",
        "text": parent_text
    }
    
    print(f"STEP 2: Injecting reply message quoting {ts_parent}...")
    reply_env = mock_envelope(reply_text, ts_reply, quote=quote)
    daemon.process_envelope(reply_env)
    
    time.sleep(2)

    # 3. Verify Comment exists under Parent Post
    print(f"STEP 3: Verifying comment exists under post {parent_post_id}...")
    resp = requests.get(f"{LEMMY_API}/comment/list", params={"post_id": parent_post_id, "sort": "New"})
    comments = resp.json().get('comments', [])
    
    found = False
    for c in comments:
        if reply_text in c['comment']['content']:
            print(f"SUCCESS: Comment found under parent post: {c['comment']['id']}")
            found = True
            break
            
    if not found:
        print("FAILURE: Reply was not posted as a comment.")
        return False

    print("\nOVERALL STATUS: PASSED")
    return True

if __name__ == "__main__":
    success = asyncio.run(run_threading_test())
    if not success:
        sys.exit(1)
