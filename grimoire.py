#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""
Grimoire - offline pentest documentation aggregator + unified search for RF-Swift.

Pulls a curated set of security knowledge bases (HackTricks, PayloadsAllTheThings,
OWASP guides, the LOTL databases, ...), indexes all their markdown/yaml into a
single full-text search index, and serves a web UI: type "ssrf", "xss", "sql",
"kerberoast", ... and it surfaces the matching docs across every source.

Subcommands:
  fetch   git clone/pull every source in sources.yaml into data/sources/
  build   (optional) run each source's native builder (mdbook/mkdocs) -> data/build/
  index   (re)build the unified SQLite FTS5 search index from markdown/yaml
  serve   start the web search interface (http://127.0.0.1:8000 by default)
  all     fetch + index   (the usual one-shot)
  mcp     expose Grimoire over the Model Context Protocol (attach Claude/etc.)

Your own docs: drop markdown into ./custom/ (auto-indexed), or add a
`type: local` entry to sources.yaml pointing at a path.

This file is a thin launcher; the implementation lives in the grimoire_app
package, split into config / model / view / controller (MVC).
"""
import os
import sys

# Make the package importable whether run as ./grimoire.py or python grimoire.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grimoire_app.controller import main

if __name__ == "__main__":
    main()
