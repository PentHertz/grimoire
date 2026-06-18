# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
import json
from grimoire_app.converters import register_handler

# ---------- Jupyter Notebooks ----------
def _notebook_to_markdown(text: str) -> str:
    """Convert a Jupyter .ipynb (JSON) into readable markdown."""
    try:
        nb = json.loads(text)
        cells = nb["cells"]
    except Exception:
        return text
    lang = "python"
    try:
        meta = nb.get("metadata", {})
        lang = (meta.get("kernelspec", {}).get("language")
                or meta.get("language_info", {}).get("name") or "python")
    except Exception:
        pass

    def _src(cell):
        s = cell.get("source", "")
        return "".join(s) if isinstance(s, list) else (s or "")

    out = []
    for cell in cells:
        ct = cell.get("cell_type")
        if ct in ("markdown", "raw"):
            chunk = _src(cell).strip()
            if chunk:
                out.append(chunk)
        elif ct == "code":
            code = _src(cell).rstrip()
            if code:
                out.append(f"```{lang}\n{code}\n```")
            for o in cell.get("outputs", []):
                txt = ""
                if o.get("output_type") == "stream":
                    t = o.get("text", "")
                    txt = "".join(t) if isinstance(t, list) else t
                elif o.get("output_type") in ("execute_result", "display_data"):
                    d = (o.get("data") or {}).get("text/plain", "")
                    txt = "".join(d) if isinstance(d, list) else d
                txt = (txt or "").rstrip()
                if txt:
                    out.append("```\n" + txt[:2000] + "\n```")
    return "\n\n".join(out)

def handle_ipynb(path: str) -> str:
    with open(path, encoding="utf-8", errors="ignore") as f:
        return _notebook_to_markdown(f.read())

register_handler(".ipynb", handle_ipynb)