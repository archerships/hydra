# Hydra Publish -- State & Development Plan
**Location:** `hydra/publish/`
**Updated:** 2026-06-08

## Overview

Publish archerships.com essays from a single source markdown file to multiple
social platforms simultaneously, with platform-optimized cover images.

All scripts live in `~/av/bin/hydra/publish/` and are symlinked into `~/.local/bin/`.
(Moved from `publish/src/` on 2026-06-11; symlinks updated.)

## Existing Publisher Scripts

| Script                  | Platform         | Mechanism       | Status                               |
| :---                    | :---             | :---            | :---                                 |
| hydra-publish           | Orchestrator     | Python          | Working; Nostr URL now auto-recorded |
| hydra-adapt-image       | Image adapter    | Python          | Written (letterbox/pillarbox)        |
| hydra-signal            | Signal           | signal-cli      | Written                              |
| nostr-publisher.py      | Nostr            | nak             | Working (kind-30023); 4 bugs fixed 2026-06-07 |
| fb-publisher.py         | Facebook         | CDP             | Rewritten 2026-06-07; strips links, attaches images, posts contact comment |
| fb-edit-post.py         | Facebook (edit)  | CDP             | Fixed viewport click bug 2026-06-07  |
| substack-publisher.py   | Substack         | Playwright/CDP  | Written, uses assembly/ dir          |
| substack-assign-section.py | Substack      | API             | Written                              |
| substack-assembly.py    | Substack         | Python          | Written (markdown assembly)          |
| x-publisher.py | Twitter Article  | Playwright/CDP  | Works; title-field bug known         |
| essay_frontmatter.py    | Shared module    | Python          | Written (read/write YAML frontmatter)|

## Script Access

- Canonical location: `hydra/publish/src/` and `hydra/ingest/src/`
- PATH access: `~/.local/bin/` symlinks to all executables
- `essay_frontmatter.py` is import-only (not executable, not symlinked)
- Do NOT use `~/av/bin/` for hydra scripts -- those copies were removed

## Changes (2026-06-08)

- Renamed `tw-article-publisher.py` to `x-publisher.py`; updated symlink and all references in orchestrator, docs, and tutorials
- Updated `tutorial-hydra-publish.md` to v1.2: added fb-edit-post.py to components table, added usage + cross-reference to tutorial-fb-edit-post.md, added Facebook troubleshooting section
- Created `tutorial-fb-edit-post.md`: full documented procedure for photo attachment via CDP (scoped file input, correct save flow, success verification)
- Created `kill-hermes` Claude Code skill (`~/.claude/commands/kill-hermes.md`)

## Nostr Bug Fixes (2026-06-07)

Four bugs fixed in the Nostr pipeline:

1. Interactive `input()` prompt blocked automation -- added `sys.stdin.isatty()` guard
2. `load_nostr_snippet()` looked in `src/content/` -- fixed to `src/essays/contact-snippet.txt`
3. `essay_to_markdown()` had `ignore_images=True` -- fixed to `False` with relative-to-absolute URL rewriting
4. `nostr-publisher.py` never output the habla.news URL -- now runs `nak key public` + `nak encode naddr` and prints `NOSTR_URL=` to stdout; `pub_nostr()` captures it and passes to `record_published()`

## Facebook Bug Fix (2026-06-07)

`remove_link_preview()` and `save_edit()` used mouse coordinates to click the
"Remove link preview" button. Button renders at y=825, below the 797px viewport.
Clicks silently failed. Fixed to use JS `element.click()` which ignores viewport.

## Status by Phase

### Phase 1 -- Wire the loop (orchestrator + state tracking)
Script: `publish/src/hydra-publish` -- EXISTS
- [x] Verify hydra-publish reads frontmatter correctly
- [x] Verify it writes back platform/url/date to published_at on success (Nostr URL now captured automatically)
- [x] Verify it skips already-published platforms (idempotent re-runs)
- [x] Verify it invokes per-platform publishers
- [ ] Verify deploy re-run after publish

### Phase 2 -- Platform image adapter
Script: `publish/src/hydra-adapt-image` -- EXISTS
- [ ] Verify letterbox/pillarbox per platform spec
- [ ] Verify duplicate detection (MD5 hash, reuses existing file)
- [ ] Verify integration with hydra-publish

### Phase 3 -- Fix Twitter Article title bug
Script: `publish/src/x-publisher.py` -- EXISTS, title selector broken
- [ ] Inspect live x.com/compose/articles DOM to find correct title selector
- [ ] Fix and test

### Phase 4 -- Nostr publisher
Script: `publish/src/nostr-publisher.py` -- EXISTS
- [x] Verify kind-30023 long-form article posting (confirmed working 2026-06-07)
- [x] Verify keychain key loading
- [x] Verify relay connectivity (damus, primal, nos.lol all succeeded)
- [ ] Verify kind-1 short announcement note posting
- [ ] Investigate Nostr client link expansion issue (X.com links in sovereign-x list expand into contact cards in some clients -- user finds it ugly; need to find markdown or formatting workaround)

### Phase 5 -- Announcement tweet
Script: NOT YET WRITTEN
- [ ] Create tw-post.py or --tweet mode in x-publisher.py
- [ ] Posts 280-char tweet with essay title + archerships.com URL + image
- [ ] Uses twitter-post adapted image (1200 x 675)

## Open Items

- Cowgorithm essay frontmatter has malformed Nostr URL (30023:pubkey/slug format written by Hermes).
  Needs updating to correct naddr:
  https://habla.news/a/naddr1qq0nyvpjxcknqd3dxqmz66rpd36x2u3dv95j6cm0wukkxmmvd3shyq3qsx7d85ccx0pc2zk99t8glywc9hsy96fj67a3lgxmxew7h35dwp8sxpqqqp65wxdg5ue
  File: src/essays/2026-06-06-halter-ai-cow-collar/2026-06-06-halter-ai-cow-collar.md

## Blockers

- Twitter Article title selector broken; needs live DOM inspection
- Nostr client link expansion: X.com profile links in kind-30023 body expand into
  contact cards in some clients (Primal, Damus). Need to find a formatting workaround.
