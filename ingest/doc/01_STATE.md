# Hydra Ingest — State & Development Plan
**Location:** `hydra/ingest/`
**Updated:** 2026-06-08

## Vision

Hydra is a bidirectional social hub with two complementary halves:

- INGEST: capture messages from Signal, Nostr, Twitter, and other sources and
  archive them in a common threaded format (Lemmy) for search and reference.
- PUBLISH: use archerships.com as the source of truth and cross-post essays
  to Twitter, Facebook, Substack, Nostr, Signal, and other platforms with
  platform-optimized images.

---

## Architecture

```
Signal ──┐                              ┌── Twitter / X
Nostr  ──┤  Nexus Bridge (sync_daemon)  ├── Facebook
Twitter ─┤  Lemmy (threaded archive)   ├── Substack
Other  ──┘                              ├── Nostr
                                        └── Signal
              archerships.com
              (source of truth for
               outbound publishing)
```

---

## Import / Sync Scripts

| Script | Platform | Mode | Mechanism | Status |
| :--- | :--- | :--- | :--- | :--- |
| substack-import | Substack | Full | substack-api | Written |
| substack-sync | Substack | Incremental | substack-api + checkpoint | Written |
| x-import | Twitter/X | Full (--full) | tw-archive.py via Nix | Written |
| x-sync | Twitter/X | Incremental | tw-archive.py via Nix | Written |
| lj-import | LiveJournal | Full + process | ljdump + import-livejournal.py | Written |
| lj-sync | LiveJournal | Incremental | ljdump (tracks checkpoint) | Written |
| fb-import | Facebook | Full | DYI JSON export parser | Written |
| fb-sync | Facebook | Incremental | Graph API (token required) or DYI | Written |
| nostr-import | Nostr | Full | nak req (all events by pubkey) | Written |
| nostr-sync | Nostr | Incremental | nak req --since checkpoint | Written |

Checkpoint files:
- Substack: `~/av/doc/substack/.last-sync`
- Facebook: `~/av/doc/facebook/.last-sync`
- Nostr: `~/av/doc/nostr/.last-import`
- Twitter/X: tracked internally by tw-archive.py (newest tweet ID per handle)
- LiveJournal: tracked internally by ljdump (lastsync in XML-RPC state)

---

## INGEST SIDE

### Phase 1 -- Complete

- [x] Nix installation on macOS, Flakes support verified
- [x] Conduit Matrix homeserver deployed via Nix Flake (RocksDB backend)
- [x] Matterbridge integrated into Nix service stack
- [x] WeeChat Matrix client configured (Python plugin via local Nix Flake)
- [x] Tutorials for Conduit, Matterbridge, and WeeChat written
- [x] sync_daemon.py and sync_groups.py written (Signal -> Lemmy bridge)
- [x] bridge_threads.db schema in place

### Phase 2 -- In progress

- [ ] Create permanent user account on local Conduit instance
- [ ] Configure Telegram bot and obtain Group IDs for real bridging
- [ ] Integrate Signal via signald (requires Nix/Docker investigation)
- [ ] Implement launchd background service management for the Nexus stack

### Phase 3 -- Planned

- [ ] Nostr ingest: subscribe to a configurable list of pubkeys/relays and
      archive kind-1 notes and kind-30023 articles into Lemmy
- [ ] Twitter/X ingest: archive tweets and threads via tw-archive script
      into Lemmy community (script: bin/hydra/ingest/tw-archive)
- [ ] Common importer format: define a canonical intermediate JSON schema so
      all ingest adapters (Signal, Nostr, Twitter) produce the same structure
      before writing to Lemmy

### Blockers (ingest)

- signald Nix packaging is not straightforward; Docker may be required
- Lemmy API rate limits need testing under real bridging load

---

## PUBLISH SIDE

### Existing scripts in bin/hydra/publish/

| Script                  | Platform         | Mechanism       | Status                                |
| :---                    | :---             | :---            | :---                                  |
| x-publisher.py | Twitter Article  | Playwright/CDP  | Works; title-field bug known          |
| fb-publisher.py         | Facebook         | Playwright/CDP  | Written, untested end-to-end          |
| substack-publisher.py   | Substack         | Playwright/CDP  | Written, uses assembly/ dir           |
| substack-sync        | Substack (ingest)| substack-api lib| Written                               |
| hydra-signal            | Signal           | signal-cli      | Written                               |

### Key architectural gap

build-essays.py already has a fully designed published_at system -- it reads
entries like {platform: twitter, url: https://...} from each essay's frontmatter
and generates "Also published at" links in the rendered HTML. But none of the
publisher scripts write back to that frontmatter after a successful publish.
The loop is never closed. As a result:

- No record of where anything has been posted
- "Also published at" links are never populated
- Every publish is entirely manual and stateless
- No protection against double-posting

### What's missing entirely

- No Nostr publisher (it's in PLATFORM_LABELS but no script exists)
- No unified entry point -- you'd have to know about and invoke 4-5 separate
  scripts per essay
- No announcement tweet (short tweet with link) separate from Twitter Article
  publishing
- No platform image adapter -- each platform requires different dimensions and
  the source image must be letterboxed to spec rather than cropped

### Publish Phase 1 -- Wire the loop (state tracking + dispatcher)

Script: bin/hydra/publish/hydra-publish (exists):
- Takes <essay-slug> and --platforms twitter,substack,facebook,nostr (or --all)
- Reads frontmatter to get title, tags, and current published_at
- Skips any platform already in published_at (idempotent re-runs)
- Calls each platform's publisher script
- On success, writes {platform: X, url: Y, date: Z} back to the frontmatter .md
- Re-runs build-essays.py --only <slug> and deploys via:
  node scripts/deploy-website.mjs --only essays,posts/img

This is the highest-leverage change -- it makes everything else coherent.

Verify: [ ]

### Publish Phase 2 -- Platform image adapter

Script: bin/hydra/publish/hydra-adapt-image (exists):
- Takes <source-image> and <platform-context>, writes adapted image to
  ~/av/tmp/hydra-img-<platform>.{png,jpg}
- Applies letterbox/pillarbox rule: scale source to fit, center on canvas,
  black fill, no cropping (pol/SOCIAL-SETTINGS.md §2)
- Integrated into hydra-publish: auto-generates the right image per platform
  before calling the platform's publisher script

  | Context              | Dimensions   | Aspect | Format  |
  | :---                 | :---         | :---   | :---    |
  | twitter-post         | 1200 x 675   | 16:9   | PNG/JPG |
  | twitter-article      | 1200 x 480   | 5:2    | PNG/JPG |
  | facebook-landscape   | 1200 x 630   | 1.91:1 | JPG     |
  | facebook-portrait    | 1080 x 1350  | 4:5    | JPG     |
  | substack-note        | 1200 x 675   | 16:9   | PNG/JPG |
  | substack-cover       | 1200 x 600   | 2:1    | JPG     |
  | nostr-short          | 1200 x 675   | 16:9   | PNG/JPG |
  | nostr-article        | 1200 x 675   | 16:9   | PNG/JPG |

Verify: [ ]

### Publish Phase 3 -- Fix Twitter Article title bug

The last session showed the title locator ([placeholder*="Title"]) times out.
Need a DOM dump from a live composer session at x.com/compose/articles to find
the correct selector. This is a one-session debugging task.

Verify: [ ]

### Publish Phase 4 -- Nostr publisher

Script: bin/hydra/publish/nostr-publisher.py (exists):
- Private key stored in system keychain via keyring, same pattern as
  substack-publisher.py
- Configurable relay list
- --kind 1: short announcement note with title, URL, image URL
- --kind 30023: full long-form article with title, tags, image, markdown body

Verify: [ ]

### Publish Phase 5 -- Announcement tweet

Add --tweet mode to x-publisher.py (or create tw-post.py):
- Posts a 280-char tweet with essay title + archerships.com URL + optional image
- Uses twitter-post adapted image (1200 x 675)
- Separate from Article publisher; this would be the default social post for
  most essays -- Article publisher is reserved for long-form essays that benefit
  from in-platform hosting

Verify: [ ]

### Blockers (publish)

- Twitter Article title selector broken; needs live DOM inspection
- Nostr publisher not written; pynostr not yet installed
- No image hosting pipeline for Nostr image URLs (need nostr.build or Arweave)
