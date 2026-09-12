# Hydra Publish — Multi-Platform Social Publishing

**Location:** `hydra/publish/`

Orchestrates cross-posting of archerships.com essays to Twitter/X Articles,
Facebook, Substack, and Nostr from a single source.

## Architecture

The pipeline is driven by a single orchestrator script. Platform-specific
publishers are thin wrappers around each platform's API or CLI. Image
adaptation is handled by a shared helper.

```
publish/src/hydra-publish            -- orchestrator: reads frontmatter, dispatches to each platform
publish/src/hydra-adapt-image        -- adapts cover image to each platform's required dimensions
publish/src/x-publisher.py  -- Twitter/X Articles publisher
publish/src/substack-publisher.py    -- Substack publisher (full post)
publish/src/nostr-publisher.py       -- Nostr publisher (kind-1 note or kind-30023 long-form article)
publish/src/hydra-signal             -- Signal adapter
publish/src/essay_frontmatter.py     -- shared module: read/write essay frontmatter
archerships.com/scripts/arweave-upload.mjs -- Arweave file uploader (used for Nostr cover images)
```

## Usage

```bash
# Publish a single essay to all platforms
hydra-publish 2026-05-27-sovereign-x --all

# Publish to specific platforms only
hydra-publish 2026-05-27-sovereign-x --platforms nostr,substack

# Dry run (shows plan without posting)
hydra-publish 2026-05-27-sovereign-x --dry-run --all
```

## Platform Details

### Twitter/X Articles

Adapter: `publish/src/x-publisher.py`

Publishes the essay as a Twitter Article (long-form). Generates an adapted
cover image at 1200x480 (5:2 aspect ratio) per `pol/TWITTER.md`.

### Facebook

Adapter: `publish/src/fb-publisher.py`

Posts to the archerships Facebook page. Cover image adapted to 1200x630
(1.91:1) per `pol/FACEBOOK.md`.

### Substack

Adapter: `publish/src/substack-publisher.py`

Publishes to Acceleration Nation section. Defaults from `pol/SUBSTACK.md`
apply (audience: everyone, email delivery: off, no tags).

### Nostr

Adapter: `publish/src/nostr-publisher.py`

Publishes as kind-30023 long-form article. Signing uses `nak` (reference
Nostr CLI by fiatjaf). Private key is loaded from the system keychain via
`keyring` and passed as `NOSTR_SECRET_KEY` env var -- never as a command
argument or written to disk.

Cover image workflow:
1. `hydra-adapt-image` produces a 1200x675 webp adapted image.
2. `scripts/arweave-upload.mjs` uploads it to Arweave via the Turbo bundler
   and returns a permanent `https://arweave.net/{txId}` URL.
3. That URL is passed to `nak` as `--tag image=URL`.

The `summary` tag is extracted from the first paragraph of the built HTML
essay and passed to `nak` as `--tag summary=...`.

## Configuration

Credentials are stored per `pol/KEYPOLICY.md`:
- Twitter, Facebook, Substack API keys: environment variables loaded from
  the system keychain at runtime.
- Nostr private key: system keychain entry `nostr-nsec`, loaded via `keyring`.
- Arweave wallet: `~/.config/arweave/wallet.json` (never committed).

Results (Nostr event ID, Substack URL, Twitter Article URL, etc.) are written
back to the essay's frontmatter under the `published_at` key by the
orchestrator.

## Known Issues

- `RelayManager.open_connections()` in pynostr blocks without a timeout. The
  nostr-publisher.py was rewritten to use `nak` instead to avoid this entirely.
- Large Arweave uploads (>3 MB manifests) can take 7-10 minutes to propagate
  through CDN77; `deploy-website.mjs` polls before updating ARNS.
