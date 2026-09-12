import json
import os
import select
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import requests

# Configuration: Lemmy
LEMMY_API = "http://localhost:8080/api/v3"
ADMIN_USER = "admin"
ADMIN_PASS = "admin_password"
MAPPING_FILE = "prj/hydra-nexus/config/mapping.json"

# Configuration: PeerTube
PEERTUBE_API = "http://peertube.localhost:8082/api/v1"
PEERTUBE_USER = "root"
PEERTUBE_PASS = "hotasapogorazesogecu"
PEERTUBE_CHANNEL_ID = 2

# Configuration: Signal
SIGNAL_ACCOUNT = "+16507736419"
SOCKET_PATH = "/tmp/signal-cli.sock"
HEARTBEAT_PATH = "/tmp/nexus-heartbeat"
ATTACHMENT_DIR = os.path.expanduser("~/.local/share/signal-cli/attachments/")
DB_PATH = "prj/hydra-nexus/data/bridge_threads.db"

class NexusDaemon:
    def __init__(self):
        self.init_db()
        self.jwt = self.lemmy_login()
        self.pt_token = self.peertube_login()
        self.mapping = self.load_mapping()
        self.last_heartbeat = 0
        self.last_poll = 0
        self.poll_in_progress = False

    def init_db(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.db = sqlite3.connect(DB_PATH)
        self.db.execute("CREATE TABLE IF NOT EXISTS threads (signal_id TEXT PRIMARY KEY, lemmy_post_id INTEGER)")
        self.db.commit()
        print(f"INFO: Database initialized at {DB_PATH}", flush=True)

    def save_thread(self, signal_id, lemmy_post_id):
        self.db.execute("INSERT OR REPLACE INTO threads (signal_id, lemmy_post_id) VALUES (?, ?)", (signal_id, lemmy_post_id))
        self.db.commit()

    def get_lemmy_post_id(self, signal_id):
        res = self.db.execute("SELECT lemmy_post_id FROM threads WHERE signal_id = ?", (signal_id,)).fetchone()
        return res[0] if res else None

    def lemmy_login(self):
        try:
            resp = requests.post(f"{LEMMY_API}/user/login", json={
                "username_or_email": ADMIN_USER,
                "password": ADMIN_PASS
            }, timeout=10)
            resp.raise_for_status()
            print("INFO: Logged into Lemmy.", flush=True)
            return resp.json()['jwt']
        except Exception as e:
            print(f"CRITICAL: Lemmy login failed: {e}", flush=True)
            sys.exit(1)

    def peertube_login(self):
        try:
            client_resp = requests.get(f"{PEERTUBE_API}/oauth-clients/local", timeout=10)
            client_resp.raise_for_status()
            client_data = client_resp.json()
            data = {
                "client_id": client_data['client_id'],
                "client_secret": client_data['client_secret'],
                "grant_type": "password",
                "username": PEERTUBE_USER,
                "password": PEERTUBE_PASS,
                "response_type": "code"
            }
            resp = requests.post(f"{PEERTUBE_API}/users/token", data=data, timeout=10)
            resp.raise_for_status()
            print("INFO: Logged into PeerTube.", flush=True)
            return resp.json()['access_token']
        except Exception as e:
            print(f"CRITICAL: PeerTube login failed: {e}", flush=True)
            sys.exit(1)

    def load_mapping(self):
        if os.path.exists(MAPPING_FILE):
            with open(MAPPING_FILE, 'r') as f:
                mapping = json.load(f)
                print(f"INFO: Loaded {len(mapping)} group mappings.", flush=True)
                return mapping
        print("WARNING: No mapping file found.", flush=True)
        return {}

    def get_community_id(self, slug):
        try:
            resp = requests.get(f"{LEMMY_API}/community", params={"name": slug}, timeout=5)
            if resp.status_code == 200:
                return resp.json()['community_view']['community']['id']
        except Exception as e:
            print(f"DEBUG: Failed to get community ID for {slug}: {e}", flush=True)
        return None

    def update_heartbeat(self):
        now = int(time.time())
        if now - self.last_heartbeat >= 10:
            try:
                with open(HEARTBEAT_PATH, 'w') as f:
                    f.write(str(now))
                self.last_heartbeat = now
            except: pass

    def is_video(self, file_path, content_type, original_name=None):
        if content_type and content_type.startswith("video/"):
            return True
        for name in [file_path, original_name]:
            if not name: continue
            ext = os.path.splitext(name)[1].lower()
            if ext in ['.mp4', '.mkv', '.mov', '.avi', '.webm']:
                return True
        return False

    def upload_to_peertube(self, file_path, title):
        print(f"DEBUG: Uploading {file_path} to PeerTube...", flush=True)
        try:
            shrink_script = str(Path(__file__).parent / "lemmy-vid-shrink")
            transcoded_path = subprocess.check_output([shrink_script, file_path], text=True).strip()
            
            with open(transcoded_path, 'rb') as f:
                files = {'videofile': ('video.mp4', f, 'video/mp4')}
                data = {
                    'name': title[:120],
                    'channelId': PEERTUBE_CHANNEL_ID,
                    'privacy': 1 # Public
                }
                resp = requests.post(f"{PEERTUBE_API}/videos/upload", 
                                     files=files, data=data,
                                     headers={"Authorization": f"Bearer {self.pt_token}"},
                                     timeout=600)
                if resp.status_code in [200, 201]:
                    video_uuid = resp.json()['video']['uuid']
                    url = f"http://peertube.localhost:8082/w/{video_uuid}"
                    
                    # Warm-up Trick: Hit the URL to ensure PeerTube is ready for Lemmy's crawler
                    try:
                        requests.get(url, timeout=10)
                    except: pass
                    
                    return url, None # No separate thumbnail logic for now
                print(f"ERROR: PeerTube {resp.status_code}: {resp.text}", flush=True)
        except Exception as e:
            print(f"ERROR: PeerTube upload: {e}", flush=True)
        return None, None

    def upload_to_pictrs(self, file_path):
        if not file_path or not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'rb') as f:
                files = {'images[]': (os.path.basename(file_path), f)}
                resp = requests.post(f"http://localhost:8080/pictrs/image", 
                                     files=files, 
                                     headers={"Authorization": f"Bearer {self.jwt}"},
                                     timeout=30)
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    if 'files' in data and len(data['files']) > 0:
                        filename = data['files'][0]['file']
                        return f"http://localhost:8080/pictrs/image/{filename}"
        except: pass
        return None

    def post_to_lemmy(self, community_slug, author, body, media_url=None, is_video=False, signal_id=None):
        community_id = self.get_community_id(community_slug)
        if not community_id: return

        title = f"Message from {author}"
        if body:
            clean_body = body.strip()
            if len(clean_body) > 0:
                title = clean_body[:50] + ("..." if len(clean_body) > 50 else "")
        else:
            body = "See attachment"
            title = f"Attachment from {author}"
        
        if media_url and not is_video:
            body = f"{body}\n\n![]({media_url})"
        
        payload = {"name": title, "community_id": community_id, "body": body}
        if media_url: payload["url"] = media_url
        
        try:
            resp = requests.post(f"{LEMMY_API}/post", json=payload, 
                         headers={"Authorization": f"Bearer {self.jwt}"}, timeout=10)
            if resp.status_code == 200:
                new_id = resp.json()['post_view']['post']['id']
                if signal_id: self.save_thread(signal_id, new_id)
                print(f"SUCCESS: Created post {new_id} for {community_slug}", flush=True)
            else:
                print(f"ERROR: Lemmy {resp.status_code}: {resp.text}", flush=True)
        except Exception as e:
            print(f"ERROR: Lemmy post: {e}", flush=True)

    def comment_to_lemmy(self, post_id, author, body, media_url=None, is_video=False):
        content = f"**Reply from {author}**\n\n{body}"
        if media_url:
            if is_video:
                content = f"{content}\n\n[Watch on PeerTube]({media_url})"
            else:
                content = f"{content}\n\n![]({media_url})"
        
        payload = {"content": content, "post_id": post_id}
        try:
            resp = requests.post(f"{LEMMY_API}/comment", json=payload, 
                         headers={"Authorization": f"Bearer {self.jwt}"}, timeout=10)
            if resp.status_code == 200:
                print(f"SUCCESS: Created comment on post {post_id}", flush=True)
            else:
                print(f"ERROR: Lemmy comment {resp.status_code}: {resp.text}", flush=True)
        except Exception as e:
            print(f"ERROR: Lemmy comment: {e}", flush=True)

    def process_envelope(self, envelope):
        msg = envelope.get('dataMessage')
        if not msg and 'syncMessage' in envelope:
            msg = envelope['syncMessage'].get('sentMessage')
        
        if not msg: return
            
        group_info = msg.get('groupInfo')
        if not group_info: return
            
        group_id = group_info.get('groupId')
        if group_id in self.mapping:
            slug = self.mapping[group_id]
            author = envelope.get('sourceName') or envelope.get('sourceNumber') or "Unknown"
            body = msg.get('message') or ""
            signal_id = f"{envelope.get('sourceNumber')}_{msg.get('timestamp')}"
            
            # Check if this is a reply
            parent_lemmy_id = None
            quote = msg.get('quote')
            if quote:
                parent_signal_id = f"{quote.get('authorNumber')}_{quote.get('id')}"
                parent_lemmy_id = self.get_lemmy_post_id(parent_signal_id)
                print(f"BRIDGE: Detected reply to Signal ID {parent_signal_id} (Lemmy: {parent_lemmy_id})", flush=True)

            media_url = None
            is_video_file = False
            attachments = msg.get('attachments', [])
            if attachments:
                att = attachments[0]
                file_name = att.get('storedFilename') or att.get('id')
                if file_name:
                    p = os.path.join(ATTACHMENT_DIR, file_name)
                    if self.is_video(p, att.get('contentType'), att.get('filename')):
                        is_video_file = True
                        media_url, _ = self.upload_to_peertube(p, body if body else f"Video from {author}")
                    else:
                        media_url = self.upload_to_pictrs(p)
            
            if parent_lemmy_id:
                self.comment_to_lemmy(parent_lemmy_id, author, body, media_url, is_video_file)
            else:
                self.post_to_lemmy(slug, author, body, media_url, is_video_file, signal_id)
        else:
            print(f"DEBUG: Ignoring message from unknown group: {group_id}", flush=True)

    def run(self):
        print("Starting Sovereign Bridge (v4.0 - Threading Support)...", flush=True)
        while True:
            client = None
            try:
                if not os.path.exists(SOCKET_PATH):
                    print(f"WAIT: Socket {SOCKET_PATH} not found.", flush=True)
                    time.sleep(5)
                    continue
                
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(SOCKET_PATH)
                client.setblocking(False)
                print("INFO: Connected to Signal socket.", flush=True)
                
                buffer = b""
                while True:
                    self.update_heartbeat()
                    now = time.time()
                    
                    if not self.poll_in_progress and now - self.last_poll >= 5:
                        client.sendall(json.dumps({"jsonrpc":"2.0","method":"receive","params":{"timeout":1},"id":"poll"}).encode() + b"\n")
                        self.last_poll = now
                        self.poll_in_progress = True
                    
                    readable, _, _ = select.select([client], [], [], 1.0)
                    if readable:
                        data = client.recv(8192)
                        if not data: break
                        buffer += data
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            line_str = line.decode('utf-8', errors='replace').strip()
                            if not line_str: continue
                            try:
                                msg_data = json.loads(line_str)
                                if msg_data.get('id') == "poll":
                                    self.poll_in_progress = False
                                    if 'result' in msg_data and isinstance(msg_data['result'], list):
                                        for item in msg_data['result']:
                                            if isinstance(item, dict) and 'envelope' in item:
                                                self.process_envelope(item['envelope'])
                                elif 'method' in msg_data and msg_data['method'] == 'receive':
                                    if 'params' in msg_data and 'envelope' in msg_data['params']:
                                        self.process_envelope(msg_data['params']['envelope'])
                                elif 'error' in msg_data:
                                    if msg_data.get('id') == "poll": self.poll_in_progress = False
                            except: pass
                                
            except Exception as e:
                print(f"RECOVERY: {e}. Retrying in 10s...", flush=True)
                self.poll_in_progress = False
                time.sleep(10)
            finally:
                if client: client.close()

if __name__ == "__main__":
    daemon = NexusDaemon()
    daemon.run()
