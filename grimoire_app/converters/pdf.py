# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
from grimoire_app.converters import register_handler, register_html_handler

# ---------- PDF ----------
def _pdf_text(path: str) -> str:
    """Extract text from PDF via pdftotext if available."""
    import subprocess
    import shutil
    if not shutil.which("pdftotext"):
        return ""
    r = subprocess.run(["pdftotext", "-q", path, "-"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _pdf_html(path: str, src: str, pathDoc: str) -> str:
    # PDFs (books, OSINT guides): parse the text with pdftotext and render
    # it as readable content (the in-iframe <embed> plugin is unreliable
    # under our strict CSP). A link still opens the original for figures.     
    import urllib.parse
    qp = (f"src={urllib.parse.quote(src)}&path={urllib.parse.quote(pathDoc)}")
    dl = (f'<p class="pdfdl"><a href="/asset?{qp}" target="_blank" '
            f'rel="noopener">open original PDF (figures/images)</a></p>')
    text = _pdf_text(path)
    body = dl
    if text.strip():
        body += _pdf_to_html(text)
    else:
        import shutil
        why = ("no extractable text (scanned/image-only PDF)"
                if shutil.which("pdftotext")
                else "install poppler-utils (pdftotext) to extract PDF text")
        body += f'<p class="pdfnote">PDF text unavailable: {why}.</p>'
    return body


def _pdf_to_html(text: str) -> str:
    """Format pdftotext output into readable, page-separated HTML. The text is
    escaped (untrusted: it comes from third-party PDFs) and wrapped per page so
    the doc viewer shows real content instead of an embedded binary."""
    import html
    pages = text.split("\f")  # pdftotext separates pages with form-feed
    parts = []
    for i, pg in enumerate(pages, 1):
        if not pg.strip():
            continue
        parts.append(f'<div class="pdfpage"><div class="pgno">page {i}</div>'
                     f"<pre>{html.escape(pg.strip(chr(10)))}</pre></div>")
    return "\n".join(parts) or "<p>(no extractable text on any page)</p>"

def handle_pdf(path: str) -> str:
    return _pdf_text(path)

def handle_pdf_html(path: str, src : str, pathDoc : str) -> str:
    """Convert PDF to HTML-friendly format (escaped unicode)."""
    return _pdf_html(path, src, pathDoc)

register_handler(".pdf", handle_pdf)
register_html_handler(".pdf", handle_pdf_html)