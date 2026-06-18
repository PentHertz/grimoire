# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Pluggable document converters: handle custom formats beyond markdown."""

from pathlib import Path
from typing import Optional, Callable

_CONVERTERS = {}
_CONVERTERS_HTML = {}

def register_handler(ext: str, handler: Callable[[str], str]):
    """Register a converter for a file extension."""
    _CONVERTERS[ext.lower()] = handler

def register_html_handler(ext: str, handler: Callable[[str], str]):
    """Register an HTML converter for a file extension."""
    _CONVERTERS_HTML[ext.lower()] = handler

def convert(f: Path) -> Optional[str]:
    """Try to convert a file to markdown. Returns None if no converter found."""
    suf = f.suffix.lower()
    handler = _CONVERTERS.get(suf)
    if handler:
        try:
            return handler(str(f))
        except Exception as e:
            print(f"[!] Converter for {suf} failed on {f.name}: {e}")
            return None
    return None

def convert_html(f: Path, src: str, pathDoc: str) -> Optional[str]:
    """Try to convert a file to HTML. Returns None if no converter found."""
    suf = f.suffix.lower()
    handler = _CONVERTERS_HTML.get(suf)
    if handler:
        try:
            return handler(str(f), src, pathDoc)
        except Exception as e:
            print(f"[!] HTML Converter for {suf} failed on {f.name}: {e}")
            return None
    return None

def load_converters():
    """Load custom converters."""
    
    # Load custom converters from converters/ folder
    import importlib.util
    from pathlib import Path as P
    
    conv_dir = P(__file__).parent / "converters"
    if not conv_dir.exists():
        return
    
    for py_file in conv_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"[!] Failed to load converter {py_file.name}: {e}")