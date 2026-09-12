#!/usr/bin/env python3
"""
substack-sort.py
Sorts downloaded Substack posts into project-specific directories
based on their section_id in metadata.json.
"""

import os
import json
import shutil

# Configuration
BASE_DIR = os.path.expanduser("~/av/doc/substack/posts")
PROJECTS_ROOT = os.path.expanduser("~/av/prj")

# Section ID to Project Mapping
MAPPING = {
    208359: "practical-seasteading",
    358240: "ai-tech-notes", # Mapping 'techbriefs' to your existing 'ai-tech-notes' orbit
    358238: "acceleration-nation"
}

def main():
    if not os.path.exists(BASE_DIR):
        print(f"Error: {BASE_DIR} not found.")
        return

    # Ensure project doc directories exist
    for project in MAPPING.values():
        os.makedirs(os.path.join(PROJECTS_ROOT, project, "doc", "substack"), exist_ok=True)

    post_slugs = [d for d in os.listdir(BASE_DIR) if os.path.isdir(os.path.join(BASE_DIR, d))]
    
    counts = {proj: 0 for proj in MAPPING.values()}
    counts["unassigned"] = 0

    for slug in post_slugs:
        post_dir = os.path.join(BASE_DIR, slug)
        meta_path = os.path.join(post_dir, "metadata.json")
        
        if not os.path.exists(meta_path):
            continue
            
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            
            section_id = meta.get("section_id")
            
            if section_id in MAPPING:
                project_name = MAPPING[section_id]
                dest_dir = os.path.join(PROJECTS_ROOT, project_name, "doc", "substack", slug)
                
                # Copy the directory to the project orbit
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                shutil.copytree(post_dir, dest_dir)
                counts[project_name] += 1
            else:
                counts["unassigned"] += 1
                
        except Exception as e:
            print(f"Error processing {slug}: {e}")

    print("\nSorting Summary:")
    for project, count in counts.items():
        print(f"  {project}: {count} posts")
    
    print(f"\nTotal posts processed: {len(post_slugs)}")
    print(f"Posts remain in {BASE_DIR} for reference.")

if __name__ == "__main__":
    main()
