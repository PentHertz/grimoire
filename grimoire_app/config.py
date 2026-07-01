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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


# Keep each source's .git by default so `update` can `git pull` incrementally
# (and skip reindexing unchanged commits). Opt in to deleting it after fetch with
# `fetch --prune-git` or GRIMOIRE_PRUNE_GIT=1 to reclaim ~40% of a shallow
# checkout - the doc viewer serves the working tree and the "view original on
# GitHub" link is built from the manifest repo URL, so neither needs .git; the
# cost is that a later fetch re-clones instead of pulling. Ideal when baking an
# offline/RF-Swift image that is never updated in place.
PRUNE_GIT = _env_flag("GRIMOIRE_PRUNE_GIT")

# Shipped (read-only) resources: web UI + the default manifest seed.
WEB_DIR = PKG_DIR / "web"
DEFAULT_SOURCES = PKG_DIR / "sources.default.yaml"
# User-editable manifest. In-repo this is the canonical sources.yaml at the root;
# installed it lives in HOME and is kept in sync with DEFAULT_SOURCES (see
# ensure_user_files): seeded on first run, refreshed on upgrade while it is
# still the untouched default, and left alone once the user has edited it.
SOURCES_FILE = (ROOT / "sources.yaml") if IN_REPO else (HOME / "sources.yaml")
# Hidden record of the packaged default we last wrote into SOURCES_FILE. Lets us
# tell an untouched seed (safe to refresh on upgrade) from local edits (keep).
SEED_MARKER = HOME / ".sources.seed.yaml"

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


def _write_seed(text: str):
    """Write the manifest and record it as the synced default."""
    SOURCES_FILE.write_text(text, encoding="utf-8")
    SEED_MARKER.write_text(text, encoding="utf-8")


def ensure_user_files():
    """When installed (not in-repo), make sure the per-user HOME exists and keep
    the editable sources.yaml in sync with the packaged default:

      * first run                       -> seed sources.yaml from the default
      * default changed on upgrade, and
        the user never edited the seed  -> refresh sources.yaml to the new default
      * the user edited sources.yaml    -> leave their manifest untouched

    The SEED_MARKER (the default we last wrote) is what disambiguates an
    untouched seed from local edits. No-op for a source checkout.
    """
    if IN_REPO:
        return
    HOME.mkdir(parents=True, exist_ok=True)
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    if not DEFAULT_SOURCES.exists():
        return
    default_text = DEFAULT_SOURCES.read_text(encoding="utf-8")

    if not SOURCES_FILE.exists():
        _write_seed(default_text)                       # first run: seed it
        return

    current = SOURCES_FILE.read_text(encoding="utf-8")
    if current == default_text:
        # already current; make sure the marker matches for clean future compares
        if not SEED_MARKER.exists() or \
                SEED_MARKER.read_text(encoding="utf-8") != default_text:
            SEED_MARKER.write_text(default_text, encoding="utf-8")
        return

    seeded = SEED_MARKER.read_text(encoding="utf-8") if SEED_MARKER.exists() else None
    if seeded is not None and current == seeded:
        _write_seed(default_text)                       # untouched seed -> adopt new default
    elif seeded is None:
        # install predating sync tracking: can't tell edits from upstream drift,
        # so keep the user's file but adopt it as the baseline; later default
        # updates will then propagate as long as it stays unedited.
        SEED_MARKER.write_text(current, encoding="utf-8")
    # else: the user has local edits -> preserve sources.yaml as-is


def reset_sources():
    """Force the user manifest back to the packaged ('official') default and
    record it as synced. Returns the manifest path, or None if there is no
    packaged default to restore from."""
    if not DEFAULT_SOURCES.exists():
        return None
    HOME.mkdir(parents=True, exist_ok=True)
    _write_seed(DEFAULT_SOURCES.read_text(encoding="utf-8"))
    return SOURCES_FILE


def write_sources(text: str):
    """Replace the user manifest with caller-supplied YAML (e.g. an imported
    file). Leaves SEED_MARKER untouched so the import counts as a local edit and
    is preserved across upgrades. Returns the manifest path."""
    HOME.mkdir(parents=True, exist_ok=True)
    SOURCES_FILE.write_text(text, encoding="utf-8")
    return SOURCES_FILE
