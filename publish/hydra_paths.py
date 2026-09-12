#!/Users/crasch/av/venv/hydra/bin/python3
"""
hydra_paths.py -- Single source of truth for hydra-publish paths.

All hydra-publish platform publishers import this module instead of
hardcoding their own path constants. If the build-scripts directory
moves, change SCRIPTS_DIR below in exactly ONE place; CONTENT_DIR,
RENDERER, DEPLOY, DST_DIR, and the rest derive from it automatically.

Environment overrides (for tests or unusual layouts):
  HYDRA_SCRIPTS_DIR  -- overrides SCRIPTS_DIR (default ~/av/bin/archerships)
  ARCHERSHIPS_ROOT   -- overrides the site project dir (default ~/av/prj/archerships.com)
  HYDRA_TMP          -- overrides the scratch dir (default ~/av/tmp)
"""

import os
from pathlib import Path

# ── THE one place to change if the build scripts move ──────────────────────
SCRIPTS_DIR = Path(os.environ.get(
    'HYDRA_SCRIPTS_DIR',
    str(Path.home() / 'av' / 'bin' / 'archerships'),
)).resolve()

# Everything below derives from SCRIPTS_DIR / the repo root.
AV_ROOT      = SCRIPTS_DIR.parent.parent                       # ~/av
CONTENT_DIR  = AV_ROOT / 'doc' / 'posts'                       # essay sources
ARCHERSHIPS  = Path(os.environ.get(
    'ARCHERSHIPS_ROOT',
    str(AV_ROOT / 'prj' / 'archerships.com'),
)).resolve()
DST_DIR      = ARCHERSHIPS / 'dst' / 'essays'                  # built essay HTML
BIN          = Path(__file__).resolve().parent                 # this dir (publishers)
TMP          = Path(os.environ.get('HYDRA_TMP', str(AV_ROOT / 'tmp'))).resolve()

# Build/render/deploy entry points (all under SCRIPTS_DIR)
BUILD        = SCRIPTS_DIR / 'build-essays.py'
RENDERER     = SCRIPTS_DIR / 'render-post.py'
DEPLOY       = SCRIPTS_DIR / 'deploy-website.mjs'
