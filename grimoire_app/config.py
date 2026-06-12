# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Grimoire configuration: filesystem paths and indexing constants.

These module attributes are the single source of truth for paths. Tests and
embedders reassign them (e.g. ``config.DATA = tmp / "data"``) and every other
module reads them as ``config.X`` at call time, so an override is seen
everywhere without re-importing.

Two layouts are supported transparently:
  * in-repo / source checkout - everything lives next to grimoire.py
    (data/, custom/, sources.yaml at the project root).
  * installed (pip / pipx) - the package ships web/ and a default manifest;
    user-writable state (data/, custom/, sources.yaml) goes in a per-user dir
    (GRIMOIRE_HOME, else $XDG_DATA_HOME/grimoire, else ~/.local/share/grimoire).
"""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent          # the grimoire_app package
ROOT = PKG_DIR.parent                              # project root (holds grimoire.py)
IN_REPO = (ROOT / "grimoire.py").is_file()

def _user_home():
    env = os.environ.get("GRIMOIRE_HOME")
    if env:
        return Path(env).expanduser()
    if IN_REPO:
        return ROOT
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "grimoire"

HOME = _user_home()
DATA = Path(os.environ.get("GRIMOIRE_DATA", HOME / "data"))
SRC_DIR = DATA / "sources"
BUILD_DIR = DATA / "build"
INDEX_DB = DATA / "index.db"
INDEX_STATE = DATA / "index_state.json"   # per-source revision -> incremental reindex
CUSTOM_DIR = HOME / "custom"

# Shipped (read-only) resources: web UI + the default manifest seed.
WEB_DIR = PKG_DIR / "web"
DEFAULT_SOURCES = PKG_DIR / "sources.default.yaml"
# User-editable manifest. In-repo this is the canonical sources.yaml at the root;
# installed it lives in HOME and is seeded from DEFAULT_SOURCES on first run.
SOURCES_FILE = (ROOT / "sources.yaml") if IN_REPO else (HOME / "sources.yaml")

TEXT_EXT = {".md", ".markdown", ".mdx", ".rst", ".yml", ".yaml"}  # .rst = Sphinx docs
# Only these may be served by /asset (prevents reading .git/config, .env, source,
# etc. from a cloned/local source via the asset endpoint).
ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico", ".pdf"}
# Files the /doc viewer and grimoire_fetch_doc may return - document types only,
# so an exposed server cannot be used to read .git/config, .env, keys, source, etc.
DOC_EXT = (TEXT_EXT | {".ipynb", ".pdf", ".json", ".txt", ".csv", ".adoc",
                       ".tkape", ".mkape"})
IGNORE_DIRS = {".git", "node_modules", "theme", "themes", ".github", "assets",
               "images", "img", "static", "site", "book"}


def ensure_user_files():
    """When installed (not in-repo), make sure the per-user HOME exists and seed
    the editable sources.yaml from the packaged default. No-op for a checkout."""
    if IN_REPO:
        return
    HOME.mkdir(parents=True, exist_ok=True)
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCES_FILE.exists() and DEFAULT_SOURCES.exists():
        SOURCES_FILE.write_text(DEFAULT_SOURCES.read_text(encoding="utf-8"),
                                encoding="utf-8")
