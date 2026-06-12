# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Grimoire - offline pentest documentation aggregator + unified search.

Split cleanly into MVC layers:
  config      filesystem paths + indexing constants (the single source of truth)
  model       data: sources manifest, fetch, index, the SQLite store + search
  view        rendering: markdown/obsidian/pdf -> HTML, banners, CSP'd pages
  controller  HTTP handler + CLI commands that wire model and view together

The runnable entrypoint is ../grimoire.py, which simply calls controller.main().
"""
__all__ = ["config", "model", "view", "controller", "mcp"]
