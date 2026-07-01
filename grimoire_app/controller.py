# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Controller layer: the HTTP request handler and the CLI commands.

Controllers do no rendering and own no SQL; they route requests/commands to the
model (data) and the view (HTML), and apply the transport-level security
controls (CSP headers, nosniff, the CSRF guard on the update endpoint).
"""
import argparse
import html
import json
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, model, view

# Live state of a web-triggered update (fetch + reindex), shared across requests.
UPDATE = {"running": False, "log": [], "rc": None}
_UPDATE_LOCK = threading.Lock()


def _start_update(only=None):
    """Kick off a background fetch+index by re-invoking this script's `all`
    command and streaming its output into UPDATE['log']. One run at a time."""
    with _UPDATE_LOCK:
        if UPDATE["running"]:
            return False
        UPDATE.update(running=True, log=[], rc=None)

    def worker():
        # works both in-repo and pip/pipx-installed
        cmd = [sys.executable, "-m", "grimoire_app", "all"]
        if only:
            cmd += ["--only", only]
        UPDATE["log"].append("$ " + " ".join(cmd))
        try:
            p = subprocess.Popen(cmd, cwd=str(config.ROOT), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in p.stdout:
                UPDATE["log"].append(line.rstrip())
                if len(UPDATE["log"]) > 600:   # keep memory bounded
                    del UPDATE["log"][:300]
            p.wait()
            UPDATE["rc"] = p.returncode
            UPDATE["log"].append(f"[=] update finished (rc={p.returncode})")
        except Exception as e:
            UPDATE["log"].append(f"[!] {e}")
            UPDATE["rc"] = -1
        finally:
            UPDATE["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            # Block cross-origin framing so a clickjacking page can't overlay the UI
            # and trigger the (custom-header-CSRF-protected) /api/update via the real
            # button. SAMEORIGIN (not DENY) so the SPA can still iframe its own /doc.
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                template = (config.WEB_DIR / "index.html").read_text(encoding="utf-8")
                page, csp = view.index_page(template)
                self._send(200, page, headers={"Content-Security-Policy": csp})
            elif u.path == "/api/search":
                self._api_search(q.get("q", [""])[0], q.get("cat", [""])[0])
            elif u.path == "/api/sources":
                self._send(200, json.dumps(model.categories()), "application/json")
            elif u.path == "/doc":
                self._doc(q.get("src", [""])[0], q.get("path", [""])[0])
            elif u.path == "/asset":
                self._asset(q.get("src", [""])[0], q.get("path", [""])[0])
            elif u.path == "/api/update":
                # CSRF guard: require a custom header the UI sets. A cross-site
                # <img>/form/simple-GET cannot set it, and we send no CORS headers,
                # so cross-origin JS can't call this state-changing endpoint either.
                if self.headers.get("X-Requested-With") != "grimoire":
                    self._send(403, '{"error":"forbidden"}', "application/json")
                    return
                started = _start_update(q.get("only", [None])[0])
                self._send(200, json.dumps({"started": started, "running": UPDATE["running"]}),
                           "application/json")
            elif u.path == "/api/update/status":
                self._send(200, json.dumps({"running": UPDATE["running"], "rc": UPDATE["rc"],
                                            "log": UPDATE["log"][-40:]}), "application/json")
            else:
                self._send(404, "not found", "text/plain")

        def _api_search(self, raw, cat):
            rows = model.search(raw, cat)
            out = [{"source": r[0], "title": r[1], "category": r[2],
                    "path": r[3], "snippet": view.escape_snippet(r[4])} for r in rows]
            self._send(200, json.dumps(out), "application/json")

        def _doc(self, src, path):
            f = model._resolve_doc(src, path)
            if not f or f.suffix.lower() not in config.DOC_EXT:
                # document types only - never .git/config, .env, keys, source files
                self._send(404, "doc not found", "text/plain")
                return
            # PDFs (books, OSINT guides): parse the text with pdftotext and render
            # it as readable content (the in-iframe <embed> plugin is unreliable
            # under our strict CSP). A link still opens the original for figures.
            if f.suffix.lower() == ".pdf":
                qp = (f"src={urllib.parse.quote(src)}&path={urllib.parse.quote(path)}")
                dl = (f'<p class="pdfdl"><a href="/asset?{qp}" target="_blank" '
                      f'rel="noopener">open original PDF (figures/images)</a></p>')
                text = model._pdf_text(f)
                if text.strip():
                    body = dl + view._pdf_to_html(text)
                else:
                    import shutil
                    why = ("no extractable text (scanned/image-only PDF)"
                           if shutil.which("pdftotext")
                           else "install poppler-utils (pdftotext) to extract PDF text")
                    body = dl + f'<p class="pdfnote">PDF text unavailable: {why}.</p>'
            elif f.suffix.lower() in (".yml", ".yaml"):
                # _read_doc_text unicode-decodes YAML so escaped accents/dashes
                # (e.g. machine-generated framework files) render as real chars
                body = "<pre>" + html.escape(model._read_doc_text(f)) + "</pre>"
            elif f.suffix.lower() == ".rst":
                # reStructuredText (Sphinx sources, e.g. PySDR): render with
                # docutils, not markdown, or directives/links come out mangled.
                body = view._rewrite_assets(
                    view._render_rst(model._read_doc_text(f)), src, path)
            else:
                # .ipynb is converted to markdown inside _read_doc_text
                body = view._rewrite_assets(
                    view._render_markdown(model._read_doc_text(f)), src, path)
            # Provenance: show the source and a link to the original upstream file.
            meta = next((s for s in model.load_sources() if s.get("name") == src), None) or {}
            stitle = meta.get("title", src)
            cat = meta.get("category", "custom")
            repo = meta.get("repo")
            origin = (repo.rstrip("/") + "/blob/HEAD/" + path) if repo else meta.get("pdf_url")
            banner = view.doc_banner(src, stitle, cat, path, origin)
            page, csp = view.doc_page(f.name, banner, body)
            self._send(200, page, headers={"Content-Security-Policy": csp})

        def _asset(self, src, path):
            # Serve an image/asset referenced by a doc (path-traversal guarded by
            # _resolve_doc). Lets <img> tags in rendered markdown display.
            f = model._resolve_doc(src, path)
            if not f or f.suffix.lower() not in config.ASSET_EXT:
                # only image/pdf assets - never .git/config, .env, source, etc.
                self._send(404, "asset not found", "text/plain")
                return
            import mimetypes
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            # Lock assets down: nosniff is set by _send; CSP sandbox so that even a
            # poisoned .html/.svg asset served here cannot execute scripts. PDFs are
            # exempt from 'sandbox' (it disables the native viewer) but stay safe:
            # nosniff + explicit application/pdf means they cannot run as HTML/JS.
            if f.suffix.lower() == ".pdf":
                hdr = {"Content-Security-Policy": "default-src 'none'",
                       "Content-Disposition": "inline"}
            else:
                hdr = {"Content-Security-Policy": "default-src 'none'; sandbox"}
            try:
                self._send(200, f.read_bytes(), ctype, headers=hdr)
            except OSError:
                self._send(404, "asset not found", "text/plain")

    return H


# --------------------------------------------------------------------------- #
# CLI commands
# --------------------------------------------------------------------------- #
def cmd_serve(args):
    if not config.INDEX_DB.exists():
        sys.exit("[!] no index yet - run: grimoire.py index  (or: grimoire.py all)")
    print(view.banner())
    srv = ThreadingHTTPServer((args.host, args.port), make_handler())
    print("  " + view._color("38;5;141", f"serving on http://{args.host}:{args.port}") +
          view._color("2", "   (Ctrl-C to stop)"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[=] bye")

def cmd_all(args):
    print(view.banner())
    model.cmd_fetch(args)
    model.cmd_index(args)


def _validate_manifest(text, where):
    """Parse a candidate manifest and sanity-check its shape before we install
    it, so a typo'd file can't silently wipe the working source list."""
    import yaml
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        sys.exit(f"[!] {where}: not valid YAML: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list) \
            or not data["sources"]:
        sys.exit(f"[!] {where}: expected a top-level 'sources:' list with entries")
    return data["sources"]


def _backup_manifest():
    """Copy the current manifest to <name>.bak before we overwrite it."""
    if config.SOURCES_FILE.exists():
        bak = config.SOURCES_FILE.with_name(config.SOURCES_FILE.name + ".bak")
        bak.write_text(config.SOURCES_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[=] backed up current manifest -> {bak}")


def cmd_sources(args):
    """Show the active manifest, or refresh it from the packaged ('official')
    default (--reset) or an input YAML file (--from). The new list takes effect
    on the next fetch/index."""
    if getattr(args, "from_file", None):
        from pathlib import Path
        src = Path(args.from_file).expanduser()
        if not src.is_file():
            sys.exit(f"[!] no such file: {src}")
        text = src.read_text(encoding="utf-8")
        n = len(_validate_manifest(text, str(src)))
        _backup_manifest()
        config.write_sources(text)
        print(f"[=] manifest replaced from {src} ({n} sources) -> {config.SOURCES_FILE}")
        print("    run `grimoire all` to fetch + index the new list")
        return

    if args.reset:
        if config.IN_REPO:
            sys.exit("[!] source checkout: sources.yaml is the canonical file here "
                     "- restore it with git, or use --from to import one")
        if not config.DEFAULT_SOURCES.exists():
            sys.exit("[!] no packaged default manifest available to reset from")
        _backup_manifest()
        path = config.reset_sources()
        n = len(_validate_manifest(path.read_text(encoding="utf-8"), str(path)))
        print(f"[=] manifest reset to the official packaged default "
              f"({n} sources) -> {path}")
        print("    run `grimoire all` to fetch + index the new list")
        return

    # default: report what is active and whether it matches the official default
    print(f"[=] active manifest: {config.SOURCES_FILE}")
    if config.SOURCES_FILE.exists():
        n = len(model.load_sources())
        print(f"    {n} sources")
        if config.DEFAULT_SOURCES.exists():
            same = (config.SOURCES_FILE.read_text(encoding="utf-8") ==
                    config.DEFAULT_SOURCES.read_text(encoding="utf-8"))
            print("    " + ("matches the official packaged default" if same else
                  "differs from the official default (local edits or a pending "
                  "update) - run `grimoire sources --reset` to restore it"))
    else:
        print("    (not created yet - run any command, or `grimoire sources --reset`)")


def main():
    config.ensure_user_files()   # seed per-user sources.yaml/custom when installed
    p = argparse.ArgumentParser(
        description="Grimoire - offline pentest docs aggregator + search.",
        epilog="by Penthertz (https://penthertz.com) - part of the RF-Swift toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="clone/update all sources")
    f.add_argument("--only", nargs="*", help="limit to these source names")
    f.add_argument("--prune-git", action="store_true",
                   help="delete each source's .git after clone to save space (~40%%). "
                        "Default keeps it for fast incremental `git pull` updates.")
    f.set_defaults(func=model.cmd_fetch)
    b = sub.add_parser("build", help="run native builders (optional)")
    b.set_defaults(func=model.cmd_build)
    i = sub.add_parser("index", help="(re)build the search index (incremental)")
    i.add_argument("--force", action="store_true", help="full rebuild, ignore change detection")
    i.set_defaults(func=model.cmd_index)
    s = sub.add_parser("serve", help="serve the web search UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)
    a = sub.add_parser("all", help="fetch + index")
    a.add_argument("--only", nargs="*")
    a.add_argument("--force", action="store_true", help="full reindex")
    a.add_argument("--prune-git", action="store_true",
                   help="delete each source's .git after clone to save space")
    a.set_defaults(func=cmd_all)
    up = sub.add_parser("update", help="refresh docs: fetch + incremental index (alias of all)")
    up.add_argument("--only", nargs="*", help="limit to these source names")
    up.add_argument("--force", action="store_true", help="full reindex")
    up.add_argument("--prune-git", action="store_true",
                    help="delete each source's .git after clone to save space")
    up.set_defaults(func=cmd_all)
    cl = sub.add_parser("clean",
                        help="remove the search index (and optionally fetched sources)")
    cl.add_argument("--sources", action="store_true",
                    help="also delete cloned sources and native builds")
    cl.add_argument("--all", action="store_true",
                    help="wipe the entire data/ directory")
    cl.set_defaults(func=model.cmd_clean)
    sc = sub.add_parser("sources",
                        help="show the active manifest, or refresh it (--reset / --from)")
    sc.add_argument("--reset", action="store_true",
                    help="restore the official packaged default manifest")
    sc.add_argument("--from", dest="from_file", metavar="FILE",
                    help="replace the manifest with this YAML file")
    sc.set_defaults(func=cmd_sources)
    m = sub.add_parser("mcp", help="serve over MCP (stdio) so an AI model can attach")
    m.add_argument("--context", metavar="FILE",
                   help="engagement context YAML (targets, hardware, SIM, RF, scope)")
    m.add_argument("--mode", choices=["read", "assist", "auto"], default="read",
                   help="read=knowledge only (default); assist=execute with per-call "
                        "approval; auto=autonomous execution (authorized use only)")
    m.add_argument("--scope", nargs="*", metavar="TARGET",
                   help="authorized targets/CIDRs; exec refuses out-of-scope hosts "
                        "(merged with context targets:)")
    m.set_defaults(func=_cmd_mcp)
    args = p.parse_args()
    args.func(args)


def _cmd_mcp(args):
    if not config.INDEX_DB.exists():
        sys.exit("[!] no index yet - run: grimoire.py index  (or: grimoire.py all)")
    from . import mcp
    mcp.cmd_mcp(args)
