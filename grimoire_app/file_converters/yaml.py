# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
from grimoire_app.converters import register_handler, register_html_handler


# ---------- YAML ----------
def _yaml_humanize(text: str) -> str:
    """Re-render YAML so escaped-unicode scalars show as real characters."""
    import re as _re
    if not _re.search(r"\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}", text):
        return text
    try:
        import yaml
        docs = list(yaml.safe_load_all(text))
    except Exception:
        return text
    docs = [d for d in docs if d is not None]
    if not docs:
        return text
    try:
        return yaml.safe_dump_all(docs, allow_unicode=True, sort_keys=False,
                                  default_flow_style=False, width=100)
    except Exception:
        return text

def handle_yaml(path: str) -> str:
    with open(path, encoding="utf-8", errors="ignore") as f:
        return _yaml_humanize(f.read())

def handle_yaml_html(path: str, src : str, pathDoc : str) -> str:
    """Convert YAML to HTML-friendly format (escaped unicode)."""
    import html
    with open(path, encoding="utf-8", errors="ignore") as f:
        return "<pre>" + html.escape(_yaml_humanize(f.read())) + "</pre>"

register_handler(".yml", handle_yaml)
register_handler(".yaml", handle_yaml)
register_html_handler(".yml", handle_yaml_html)
register_html_handler(".yaml", handle_yaml_html)