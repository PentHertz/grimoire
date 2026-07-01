# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""View layer: turn raw docs into safe HTML and assemble CSP-hardened pages.

Nothing here touches the database or the filesystem state; it only renders.

Two distinct XSS defenses, by content type: snippets, PDF text and YAML are
HTML-escaped before insertion; rendered *markdown* bodies, by contrast, may
carry raw HTML (python-markdown passes it through) and are neutralized instead
by the strict per-load nonce CSP every page ships - `script-src 'nonce-<rnd>'`
with no `unsafe-inline`, `object-src`/`base-uri`/`form-action` locked down - so
injected markup from a poisoned source cannot execute. The CSP is therefore a
load-bearing control for doc rendering, not just defense-in-depth.
"""
import html
import re as _re
import secrets
import urllib.parse

# --------------------------------------------------------------------------- #
# Branding
# --------------------------------------------------------------------------- #
_ART = r"""
   ____      _                 _
  / ___|_ __(_)_ __ ___   ___ (_)_ __ ___
 | |  _| '__| | '_ ` _ \ / _ \| | '__/ _ \
 | |_| | |  | | | | | | | (_) | | | |  __/
  \____|_|  |_|_| |_| |_|\___/|_|_|  \___|
"""

def _color(code, s):
    import sys
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s

def banner() -> str:
    return (
        _color("38;5;141", _ART) + "\n"
        "  " + _color("38;5;141", "offensive knowledge, offline") +
        "  " + _color("2", "- one search box for every playbook") + "\n"
        "  " + _color("38;5;75", "Penthertz") + _color("2", "  https://penthertz.com") +
        _color("2", "   |   part of the RF-Swift toolkit") + "\n"
    )


# --------------------------------------------------------------------------- #
# Markdown / Obsidian / asset rendering
# --------------------------------------------------------------------------- #
def _obsidian_preprocess(text: str) -> str:
    """Make Obsidian-authored notes render cleanly: strip YAML frontmatter,
    turn [[wiki|links]] into searches, and style #tags. Harmless on plain md."""
    # strip leading YAML frontmatter
    text = _re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=_re.S)
    # embeds ![[x]] -> plain reference (we can't inline the asset)
    text = _re.sub(r"!\[\[([^\]]+)\]\]", r"`\1`", text)
    # [[target|alias]] and [[target]] -> link that searches for the target (breaks out of the iframe)
    def _wl(m):
        inner = m.group(1)
        target, _, alias = inner.partition("|")
        target = target.split("#")[0].strip()
        label = (alias or inner).strip()
        return f'<a href="/?q={urllib.parse.quote(target)}" target="_top">{html.escape(label)}</a>'
    text = _re.sub(r"\[\[([^\]]+)\]\]", _wl, text)
    # #tags (not headings, not in code) -> styled span, also searchable
    text = _re.sub(r"(?<=\s)#([A-Za-z0-9_][\w/-]*)",
                   lambda m: f'<a class="tag" href="/?q={urllib.parse.quote(m.group(1))}" target="_top">#{html.escape(m.group(1))}</a>',
                   text)
    return text

try:
    import nh3 as _nh3
    # Extend nh3's (ammonia) safe defaults with the few attributes our rendering
    # relies on: heading/toc ids, and the tag/wikilink anchors' class + target.
    _SANITIZE_TAGS = set(_nh3.ALLOWED_TAGS) | {"mark"}
    _SANITIZE_ATTRS = {k: set(v) for k, v in _nh3.ALLOWED_ATTRIBUTES.items()}
    _SANITIZE_ATTRS.setdefault("a", set()).update({"class", "target", "id"})
    _SANITIZE_ATTRS.setdefault("img", set()).update({"class"})
    for _t in ("h1", "h2", "h3", "h4", "h5", "h6", "span", "div", "code", "pre",
               "table", "td", "th", "tr", "li", "ol", "ul", "p", "blockquote"):
        _SANITIZE_ATTRS.setdefault(_t, set()).update({"class", "id"})
except ImportError:                       # optional: the doc CSP is the primary guard
    _nh3 = None

def _sanitize_html(rendered: str) -> str:
    """Second XSS layer under the doc CSP: strip <script>/<style>/<meta>, event
    handlers and dangerous URL schemes from rendered markdown. No-op (CSP-only)
    when nh3 isn't installed, matching the graceful markdown fallback."""
    if _nh3 is None:
        return rendered
    return _nh3.clean(rendered, tags=_SANITIZE_TAGS, attributes=_SANITIZE_ATTRS)

def _render_markdown(text: str) -> str:
    text = _obsidian_preprocess(text)
    try:
        import markdown
    except ImportError:
        return "<pre>" + html.escape(text) + "</pre>"
    return _sanitize_html(markdown.markdown(
        text, extensions=["fenced_code", "tables", "toc", "sane_lists"]))

def _rewrite_assets(body: str, src: str, relpath: str) -> str:
    """Make a doc's relative links work in the viewer: rewrite relative <img>
    src to the /asset endpoint (so images display) and relative links to other
    .md files to /doc (so internal navigation works). Absolute/external/anchor
    URLs are left untouched."""
    import posixpath
    docdir = posixpath.dirname(relpath)
    qt = urllib.parse.quote
    ext = _re.compile(r'^(https?:|//|data:|#|mailto:)', _re.I)  # truly external/anchor

    def resolve(url):
        # repo-root-absolute (/x) -> from repo root; else relative to the doc dir
        rel = url.lstrip("/") if url.startswith("/") else posixpath.join(docdir, url)
        return posixpath.normpath(rel).lstrip("/")

    def img(m):
        url = m.group(1)
        return m.group(0) if ext.match(url) else \
            f'src="/asset?src={qt(src)}&path={qt(resolve(url))}"'

    def link(m):
        url = m.group(1)
        if ext.match(url):
            return m.group(0)
        base = url.split("#")[0]
        if base.lower().endswith((".md", ".markdown")):
            return f'href="/doc?src={qt(src)}&path={qt(resolve(base))}"'
        return m.group(0)

    body = _re.sub(r'src="([^"]+)"', img, body)
    body = _re.sub(r'href="([^"]+)"', link, body)
    return body

def _pdf_to_html(text: str) -> str:
    """Format pdftotext output into readable, page-separated HTML. The text is
    escaped (untrusted: it comes from third-party PDFs) and wrapped per page so
    the doc viewer shows real content instead of an embedded binary."""
    pages = text.split("\f")  # pdftotext separates pages with form-feed
    parts = []
    for i, pg in enumerate(pages, 1):
        if not pg.strip():
            continue
        parts.append(f'<div class="pdfpage"><div class="pgno">page {i}</div>'
                     f"<pre>{html.escape(pg.strip(chr(10)))}</pre></div>")
    return "\n".join(parts) or "<p>(no extractable text on any page)</p>"


# --------------------------------------------------------------------------- #
# Search-result snippet
# --------------------------------------------------------------------------- #
def escape_snippet(s: str) -> str:
    """FTS5 marks matches with char(2)/char(3) sentinels; escape everything else
    so doc content (from possibly-poisoned repos) can never inject markup, then
    restore only the <mark> highlight."""
    return (html.escape(s or "").replace("\x02", "<mark>")
            .replace("\x03", "</mark>"))


# --------------------------------------------------------------------------- #
# Whole-page assembly (with CSP)
# --------------------------------------------------------------------------- #
_DOC_STYLE = ("<style>"
    "body{background:#0e0f13;color:#d7dadf;font:15px/1.6 system-ui,sans-serif;"
    "max-width:900px;margin:0 auto;padding:24px 32px}"
    "a{color:#79b8ff}"
    "h1,h2,h3{color:#c9a3ff;border-bottom:1px solid #23262e;padding-bottom:.2em}"
    "code{background:#16181d;border-radius:6px;padding:.1em .35em}"
    "pre{background:#16181d;border-radius:6px;padding:12px;overflow:auto;"
    "border:1px solid #23262e}"
    "table{border-collapse:collapse}td,th{border:1px solid #23262e;padding:6px 10px}"
    "mark{background:#5b4b00;color:#ffe08a}"
    "blockquote{border-left:3px solid #3a3f4b;margin:0;padding-left:14px;color:#9aa0aa}"
    "a.tag{color:#c9a3ff;background:#1d1830;border:1px solid #2c2740;"
    "border-radius:4px;padding:0 5px;font-size:.85em;text-decoration:none}"
    ".origin{position:sticky;top:0;background:#14161c;border:1px solid #23262e;"
    "border-radius:8px;padding:8px 12px;margin:-8px 0 20px;font-size:12px;"
    "color:#9aa0aa;display:flex;align-items:center;gap:8px;flex-wrap:wrap}"
    ".origin b{color:#d7dadf}.origin .sep{color:#3a3f4b}"
    ".origin .badge{background:#1d1830;border:1px solid #2c2740;color:#c9a3ff;"
    "border-radius:4px;padding:1px 7px;font-weight:600}"
    ".origin .orig{margin-left:auto;color:#79b8ff;text-decoration:none;"
    "border:1px solid #23262e;border-radius:5px;padding:2px 9px}"
    ".origin .orig:hover{border-color:#79b8ff}"
    "pre{position:relative}"
    ".cp{position:absolute;top:6px;right:6px;font:11px system-ui;color:#9aa0aa;"
    "background:#23262e;border:1px solid #3a3f4b;border-radius:5px;padding:2px 8px;"
    "cursor:pointer;opacity:0;transition:.1s}pre:hover .cp{opacity:1}"
    ".cp:hover{color:#fff}.cp.ok{color:#7ee787;border-color:#2ea043}"
    ".pdfpage{margin:0 0 18px}.pdfpage pre{white-space:pre-wrap;word-wrap:break-word}"
    ".pgno{font-size:11px;color:#8b91a0;text-transform:uppercase;letter-spacing:.4px;"
    "margin:0 0 4px}.pdfdl{margin:0 0 18px}"
    ".pdfnote{color:#9aa0aa;background:#16181d;border:1px solid #23262e;"
    "border-radius:6px;padding:12px}"
    "</style>")

# Copy-to-clipboard on every code block (commands/snippets). Uses the async
# clipboard API on secure/localhost contexts, with a legacy execCommand fallback
# for plain-HTTP/LAN access.
_COPY_JS = (
    "document.querySelectorAll('pre').forEach(function(p){"
    "var b=document.createElement('button');b.className='cp';b.textContent='copy';"
    "b.onclick=function(){var t=(p.querySelector('code')||p).innerText;"
    "var done=function(){b.textContent='copied';b.classList.add('ok');"
    "setTimeout(function(){b.textContent='copy';b.classList.remove('ok');},1200);};"
    "if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done);}"
    "else{var ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);"
    "ta.select();try{document.execCommand('copy');}catch(e){}ta.remove();done();}};"
    "p.appendChild(b);});")

def doc_banner(src, stitle, cat, path, origin) -> str:
    """Provenance banner shown atop every doc: source, title, category, path,
    and a link to the original upstream file."""
    banner = (f'<div class="origin"><span class="badge">{html.escape(src)}</span>'
              f'<b>{html.escape(stitle)}</b><span class="sep">/</span>'
              f'{html.escape(cat)}<span class="sep">/</span>'
              f'<code>{html.escape(path)}</code>')
    if origin:
        banner += (f'<a class="orig" href="{html.escape(origin)}" target="_blank" '
                   f'rel="noopener">view original on GitHub</a>')
    return banner + "</div>"

def doc_page(fname: str, banner_html: str, body: str):
    """Assemble a full doc page and its CSP. Returns (page_html, csp_header).
    Only this page's nonce'd <script> runs; any <script>/on*-handler/javascript:
    URI injected by a poisoned doc is blocked. PDFs render as parsed text, so no
    object/embed is needed and object-src stays 'none'."""
    nonce = secrets.token_urlsafe(16)
    script = f'<script nonce="{nonce}">{_COPY_JS}</script>'
    page = (f"<!doctype html><meta charset=utf-8>"
            f"<title>{html.escape(fname)}</title>{_DOC_STYLE}"
            f"<article class=doc>{banner_html}{body}</article>{script}")
    csp = ("default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
           f"script-src 'nonce-{nonce}'; object-src 'none'; frame-src 'none'; "
           "connect-src 'none'; base-uri 'none'; form-action 'none'; "
           "frame-ancestors 'self'")   # only the same-origin SPA may frame a doc
    return page, csp

def index_page(template_html: str):
    """Inject a per-load nonce into the SPA template and return (page, csp).
    The page's own inline <script> is allowed; injected doc markup cannot run."""
    nonce = secrets.token_urlsafe(16)
    page = template_html.replace("<script>", f'<script nonce="{nonce}">', 1)
    csp = ("default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
           f"script-src 'nonce-{nonce}'; connect-src 'self'; frame-src 'self'; "
           "base-uri 'none'; form-action 'none'; "
           "frame-ancestors 'none'")   # the search UI (Update button) is never framed
    return page, csp
