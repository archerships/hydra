#!/Users/crasch/av/venv/hydra/bin/python3
"""
nostr-publisher.py -- Publish to Nostr via nak.

Supports kind-1 short notes and kind-30023 long-form articles.
Private key is stored in the system keychain (never on disk or in process args).

Usage:
  # Short announcement note (kind 1)
  nostr-publisher.py --kind 1 --content "text" [--url URL] [--image-url URL] [--tags t1,t2]

  # Read note content from a plain-text file
  nostr-publisher.py --kind 1 --file note.txt [--url URL]

  # Long-form article (kind 30023)
  nostr-publisher.py --kind 30023 --file essay.md --title "Title" --slug my-article
                     [--summary "..."] [--url URL] [--image-url URL] [--tags t1,t2]

  # Inspect event JSON without broadcasting
  nostr-publisher.py --dry-run --kind 1 --content "test"

Store private key in keychain (one-time setup):
  python3 -c "import keyring; keyring.set_password('nostr', 'private_key', 'your_hex_key')"

Requirements:
  pip install keyring
  go install github.com/fiatjaf/nak@latest  (binary must be on PATH or at ~/go/bin/nak)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import keyring

sys.stdout.reconfigure(line_buffering=True)

DEFAULT_RELAYS = [
    'wss://relay.damus.io',
    'wss://relay.primal.net',
    'wss://nos.lol',
    'wss://relay.nostr.band',
]


def find_nak() -> str:
    nak = shutil.which('nak') or os.path.expanduser('~/go/bin/nak')
    if not Path(nak).exists():
        sys.exit(
            'nak not found. Install with:\n'
            '  go install github.com/fiatjaf/nak@latest\n'
            'Then ensure ~/go/bin is on your PATH.'
        )
    return nak


def load_hex_key() -> str:
    hex_key = keyring.get_password('nostr', 'private_key')
    if not hex_key:
        sys.exit(
            'Nostr private key not found in keychain.\n'
            'Store it with:\n'
            "  python3 -c \"import keyring; "
            "keyring.set_password('nostr', 'private_key', 'your_hex_key')\""
        )
    return hex_key.strip()


def build_nak_cmd(nak: str, args, content_file: Path, relays: list[str]) -> list[str]:
    cmd = [nak, 'event',
           '--kind', str(args.kind),
           '--content', f'@{content_file}']

    if args.kind == 30023:
        if not args.title:
            sys.exit('--title is required for kind 30023')
        if not args.slug:
            sys.exit('--slug is required for kind 30023')
        cmd += ['-d', args.slug]
        cmd += ['--tag', f'title={args.title}']
        cmd += ['--tag', f'published_at={int(time.time())}']
        if args.url:
            cmd += ['--tag', f'r={args.url}']
        if args.summary:
            cmd += ['--tag', f'summary={args.summary}']
        if args.image_url:
            cmd += ['--tag', f'image={args.image_url}']
    else:
        # kind 1: url appended to content (already done when writing content_file)
        if args.image_url:
            cmd += ['--tag', f'image={args.image_url}']

    if args.tags:
        for t in (t.strip() for t in args.tags.split(',') if t.strip()):
            cmd += ['--tag', f't={t}']

    if not args.dry_run:
        cmd += relays

    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Publish a note or long-form article to Nostr via nak.'
    )
    parser.add_argument('--kind', type=int, default=1, choices=[1, 30023],
                        help='Event kind: 1=short note, 30023=long-form article (default: 1)')
    parser.add_argument('--content', '-c',
                        help='Post content (plain text or markdown for kind 30023)')
    parser.add_argument('--file', '-f',
                        help='Read content from file (overrides --content)')
    parser.add_argument('--url',
                        help='Canonical URL; appended to kind-1 body, r-tag in kind-30023')
    parser.add_argument('--image-url',
                        help='HTTPS URL of cover image (must be publicly accessible)')
    parser.add_argument('--title',
                        help='Article title (required for kind 30023)')
    parser.add_argument('--slug',
                        help='Unique article identifier / d-tag (required for kind 30023)')
    parser.add_argument('--summary',
                        help='Short summary (kind 30023 only)')
    parser.add_argument('--tags',
                        help='Comma-separated hashtags without # (e.g. privacy,bitcoin)')
    parser.add_argument('--relays',
                        help='Comma-separated relay WSS URLs (default: built-in list)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print event JSON without broadcasting')
    args = parser.parse_args()

    if not args.content and not args.file:
        parser.error('Provide --content or --file')

    nak = find_nak()
    hex_key = load_hex_key()
    relays = [r.strip() for r in args.relays.split(',')] if args.relays else DEFAULT_RELAYS

    # Build content string
    if args.file:
        content = Path(os.path.expanduser(args.file)).read_text(encoding='utf-8').strip()
    else:
        content = args.content or ''

    # For kind 1, append URL to body
    if args.kind == 1 and args.url:
        content = f'{content}\n\n{args.url}' if content else args.url

    # Write content to a temp file (nak reads via @path; avoids all quoting issues)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                     delete=False, encoding='utf-8') as tf:
        tf.write(content)
        content_file = Path(tf.name)

    try:
        cmd = build_nak_cmd(nak, args, content_file, relays)
        env = os.environ.copy()
        env['NOSTR_SECRET_KEY'] = hex_key

        if args.dry_run:
            print('nak command (key redacted):')
            print(' '.join(cmd))
            print()

        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)

        if result.returncode == 0 and args.kind == 30023 and not args.dry_run and args.slug:
            pk = subprocess.run([nak, 'key', 'public', hex_key],
                                capture_output=True, text=True)
            pubkey = pk.stdout.strip()
            if pubkey:
                naddr = subprocess.run(
                    [nak, 'encode', 'naddr',
                     '--kind', '30023',
                     '--pubkey', pubkey,
                     '--identifier', args.slug],
                    capture_output=True, text=True)
                if naddr.returncode == 0:
                    print(f'NOSTR_URL=https://yakihonne.com/article/{naddr.stdout.strip()}')
                else:
                    print(f'WARNING: nak encode naddr failed: {naddr.stderr.strip()}', file=sys.stderr)
                    print(f'Run manually: nak key public <hex_key> | xargs -I{{}} nak encode naddr --kind 30023 --pubkey {{}} --identifier {args.slug}')
            else:
                print('WARNING: nak key public returned empty output; cannot compute naddr URL', file=sys.stderr)

        sys.exit(result.returncode)
    finally:
        content_file.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
