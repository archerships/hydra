import os
import json
import subprocess
import requests
import re

LEMMY_API = "http://localhost:8080/api/v3"
ADMIN_USER = "admin"
ADMIN_PASS = "admin_password"
MAPPING_FILE = "prj/hydra-nexus/config/mapping.json"

def normalize_name(name):
    if not name or name == "null":
        return None
    # Remove special characters and spaces, lowercase
    clean = re.sub(r'[^a-zA-Z0-9\s]', '', name)
    clean = clean.lower().strip().replace(' ', '_')
    return clean[:20].strip('_')

def get_signal_groups():
    print("Fetching Signal groups...")
    result = subprocess.run(['signal-cli', '-u', '+16507736419', 'listGroups'], capture_output=True, text=True)
    groups = []
    for line in result.stdout.split('\n'):
        if line.startswith('Id:'):
            # Parse line: Id: ... Name: ... Active: ...
            match = re.search(r'Id: (.*?) Name: (.*?)  Active:', line)
            if match:
                groups.append({'id': match.group(1), 'name': match.group(2)})
    return groups

def lemmy_login():
    print("Logging in to Lemmy...")
    resp = requests.post(f"{LEMMY_API}/user/login", json={
        "username_or_email": ADMIN_USER,
        "password": ADMIN_PASS
    })
    resp.raise_for_status()
    return resp.json()['jwt']

def create_community(jwt, name, title):
    print(f"Creating community: {name} ({title})...")
    resp = requests.post(f"{LEMMY_API}/community", json={
        "name": name,
        "title": title
    }, headers={
        "Authorization": f"Bearer {jwt}"
    })
    if resp.status_code == 200:
        print(f"SUCCESS: Created {name}")
        return True
    elif resp.status_code == 400 and "community_already_exists" in resp.text:
        print(f"INFO: {name} already exists")
        return True
    else:
        print(f"ERROR creating {name}: {resp.text}")
        return False

def main():
    try:
        jwt = lemmy_login()
        groups = get_signal_groups()
        
        mapping = {}
        if os.path.exists(MAPPING_FILE):
            with open(MAPPING_FILE, 'r') as f:
                mapping = json.load(f)

        for group in groups:
            slug = normalize_name(group['name'])
            if not slug:
                continue
            
            if create_community(jwt, slug, group['name']):
                mapping[group['id']] = slug

        # Save mapping
        with open(MAPPING_FILE, 'w') as f:
            json.dump(mapping, f, indent=2)
        print(f"Mapping saved to {MAPPING_FILE}")

    except Exception as e:
        print(f"EXCEPTION: {str(e)}")

if __name__ == "__main__":
    main()
