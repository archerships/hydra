#!/Users/crasch/av/venv/hydra/bin/python3
import os
import markdown2
import sys

# Configuration
TUTORIAL_DIR = os.path.expanduser("~/av/doc")
ASSEMBLY_DIR = os.path.expanduser("~/av/doc/ai/substack/assembly")
TUTORIALS = [
    "tutorial-ssh-phone-laptop.md",
    "tutorial-gemini-remote-access.md",
    "tutorial-tailscale-setup.md",
    "tutorial-phone-filesystem-mount.md"
]

def assemble_post(filename):
    print(f"Assembling Substack content for {filename}...")
    
    input_path = os.path.join(TUTORIAL_DIR, filename)
    if not os.path.exists(input_path):
        print(f"Error: {filename} not found.")
        return

    # Read Markdown content
    with open(input_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # Extract Title (assuming it's the first # Header)
    title = "Untitled Tutorial"
    for line in md_content.split('\n'):
        if line.startswith('# '):
            title = line[2:].strip()
            break

    # Convert to HTML (Substack handles HTML paste best)
    # extras=["tables"] ensures Markdown tables are converted correctly
    html_content = markdown2.markdown(md_content, extras=["tables"])

    # Define output path
    output_filename = filename.replace(".md", ".html")
    output_path = os.path.join(ASSEMBLY_DIR, output_filename)

    # Save HTML assembly
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"<!-- TITLE: {title} -->\n")
        f.write(html_content)

    print(f"Successfully assembled {output_path}")

def main():
    if not os.path.exists(ASSEMBLY_DIR):
        os.makedirs(ASSEMBLY_DIR)
        print(f"Created directory: {ASSEMBLY_DIR}")

    for tutorial in TUTORIALS:
        assemble_post(tutorial)

if __name__ == "__main__":
    main()
