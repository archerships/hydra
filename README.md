# Hydra

Bidirectional social hub. Two halves, one project.

Hydra is a unified platform for both consuming and publishing social content,
with archerships.com as the source of truth for outbound publishing.

```
                          +───────────────────────+
                          |    archerships.com    |
                          |   (source of truth)   |
                          +───────+───────+───────+
                                  |       |
                    +─────────────+       +─────────────+
                    v                                   v
          +─────────────────+               +──────────────────────+
          |    INGEST       |               |       PUBLISH        |
          |  (hydra/ingest) |               |    (hydra/publish)   |
          |                 |               |                      |
          | Capture from:   |               | Publish to:          |
          |  Signal         |               |  Twitter/X Articles  |
          |  Nostr          |               |  Facebook            |
          |  Twitter        |               |  Substack            |
          |  Matrix/Conduit |               |  Nostr               |
          |                 |               |  Signal              |
          | Archive in:     |               |                      |
          |  Lemmy          |               | Image adaptation:    |
          |  (threaded)     |               |  Letterbox to spec   |
          +─────────────────+               +──────────────────────+
```

---

## Directory Structure

```
hydra/
  README.md                        -- this file
  ingest/
    doc/
      01_STATE.md                  -- ingest current state and roadmap
    src/
      sync_daemon.py               -- Signal -> Lemmy bridge daemon
      sync_groups.py               -- group sync helper
      tw-archive                   -- archive a Twitter account locally
      tw-archive.old               -- prior version kept for reference
      tw-list-feed                 -- pull a Twitter list feed
      tw-to-rss                    -- convert Twitter feed to RSS
      substack-import              -- full historical sync: substack-sync + sort
      substack-sync             -- incremental sync: only posts newer than checkpoint
      substack-sort.py             -- sort downloaded Substack posts by section
      x-import                     -- full Twitter/X archive via tw-archive --full
      x-sync                       -- incremental Twitter/X sync (new tweets only)
      lj-import                    -- full LiveJournal dump via ljdump + import-livejournal.py
      lj-sync                      -- incremental LiveJournal sync (ljdump tracks checkpoint)
      fb-import                 -- import Facebook DYI JSON export
      fb-sync                   -- incremental Facebook Page sync (Graph API or DYI re-export)
      nostr-import              -- fetch all Nostr events by pubkey via nak
      nostr-sync                -- incremental Nostr sync since last checkpoint
      export-twitter-lists-to-bookmarks.py
      merge-twitter-bookmarks.py
      rediscover-twitter-lists.py
      twitter-video-converter.sh   -- convert Twitter video formats
      lemmy-vid-shrink             -- transcode video before Lemmy/PeerTube upload
      lemmy-purge                  -- purge content from local Lemmy instance
      tests/
        conftest.py                -- pytest fixtures (BIN -> ingest/src/)
        test_full_pipeline.py
        test_login.py
        test_robust_video_pipeline.py
        test_signal_to_lemmy_full.py
        test_signal_to_lemmy_video.py
        test_threading.py
        check_lemmy.py
        verify_sort.py
        verify_video_playback.py
    config/
      lemmy.hjson
      mapping.json
      nginx.conf
    service/
      conduit.toml
      matterbridge.toml
      flake.nix / flake.lock
    client/
      flake.nix / flake.lock       -- WeeChat Matrix client Nix flake
    data/                          -- runtime data (gitignored)
  publish/
    doc/
      01_STATE.md                  -- publish current state and roadmap
      README.md                    -- publish architecture detail
    art/                           -- cover images and social card assets
    log/                           -- session logs
    src/
      hydra-publish                -- orchestrator: reads frontmatter, dispatches
      hydra-adapt-image            -- letterbox source image to platform dimensions
      hydra-signal                 -- Signal adapter (signal-cli)
      nostr-publisher.py           -- Nostr kind-1 / kind-30023 publisher
      substack-publisher.py        -- Substack publisher (Playwright/CDP)
      substack-assign-section.py   -- assign section to existing Substack post
      substack-assembly.py         -- assemble markdown for Substack
      x-publisher.py      -- Twitter/X Article publisher (Playwright/CDP)
      essay_frontmatter.py         -- shared module: read/write essay frontmatter
      tests/
        conftest.py                -- pytest fixtures (BIN -> publish/src/)
        test_adapt_image.py
        test_hydra_publish.py
        test_nostr_publisher.py
```

---

## Script Access

All scripts are symlinked into `~/.local/bin/` for PATH access. Do not invoke
scripts via `~/av/bin/` -- those copies no longer exist. The canonical locations
are `publish/src/` and `ingest/src/` only.

To add a new script: place it in the appropriate `src/` directory, then:

```bash
ln -sf ~/av/prj/hydra/publish/src/<script> ~/.local/bin/<script>
# or for ingest:
ln -sf ~/av/prj/hydra/ingest/src/<script> ~/.local/bin/<script>
```

`essay_frontmatter.py` is a shared import module used by publisher scripts in
`publish/src/`. It is not symlinked to `~/.local/bin/` and must not be invoked
directly.

---

## Running Tests

```bash
# publish-side tests
cd ~/av/prj/hydra/publish/src/tests && pytest

# ingest-side tests
cd ~/av/prj/hydra/ingest/src/tests && pytest
```

Each `tests/` directory has its own `conftest.py` whose `BIN` constant resolves
to the parent `src/` directory via `Path(__file__).parent.parent`. Tests must be
run from within the tests directory or with the tests directory as the pytest
root so that conftest.py is discovered correctly.

---

## Key Configuration

Credentials follow `pol/KEYPOLICY.md` -- never in plaintext files:
- Twitter, Facebook, Substack: system keychain via `keyring`
- Nostr private key: system keychain entry `nostr / private_key`
- Arweave wallet: `~/.config/arweave/wallet.json` (never committed)

Image dimensions and formatting follow:
- `pol/SOCIAL-SETTINGS.md` -- shared defaults (letterbox rule, format selection)
- `pol/TWITTER.md`, `pol/FACEBOOK.md`, `pol/SUBSTACK.md`, `pol/NOSTR.md`

Published state (platform URL, date, ID) is written back to each essay's
frontmatter under `published_at` by `hydra-publish` after a successful post.

---

## State and Roadmap

See `ingest/doc/01_STATE.md` and `publish/doc/01_STATE.md` for current status,
open tasks, and blockers for each side.
