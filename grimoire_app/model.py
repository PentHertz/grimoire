# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""Model layer: the sources manifest, fetching, indexing, and the search store.

All SQLite access is funnelled through the ``Index`` class, whose every method
uses parameterized queries (placeholders, never string-formatted user input).
Free-text search additionally passes through ``_fts_query`` which reduces the
query to alphanumeric prefix tokens before it can reach a MATCH expression, so a
poisoned query can neither break out of the SQL nor the FTS5 grammar.
"""
import json
import hashlib
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import urllib.parse
from pathlib import Path
from html.parser import HTMLParser

from . import config

# Accept only ordinary remote git URL schemes. This blocks git's local/transport
# helpers that can execute commands at clone time - notably `ext::sh -c ...` and
# `file://`/`fd::` - and rejects URLs starting with `-` (git option injection).
def _safe_repo_url(url):
    if not isinstance(url, str) or not url:
        return False
    if url.startswith(("https://", "http://", "git://", "ssh://")):
        return True
    return re.match(r"^git@[A-Za-z0-9._-]+:", url) is not None


# --------------------------------------------------------------------------- #
# Sources manifest
# --------------------------------------------------------------------------- #
def load_sources():
    try:
        import yaml  # PyYAML
    except ImportError:
        sys.exit("[!] PyYAML is required to read sources.yaml: pip install pyyaml")
    path = config.SOURCES_FILE
    if not path.exists() and config.DEFAULT_SOURCES.exists():
        path = config.DEFAULT_SOURCES          # installed but not yet seeded
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("sources", [])

def _linked_sources():
    """Return synthetic source entries for mirrored outbound-link documents.

    Each mirrored source lives at data/linked/<source>-links with a manifest.json
    written by `grimoire links fetch`. Keeping them synthetic avoids mutating the
    user's sources.yaml while still making the normal index/doc/MCP path work.
    """
    out = []
    root = config.LINK_DIR
    if not root.exists():
        return out
    for manifest in sorted(root.glob("*/manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name") or manifest.parent.name
        out.append({
            "name": name,
            "title": data.get("title") or name,
            "category": data.get("category") or "linked",
            "type": "linked",
            "path": str(manifest.parent),
            "docs_dir": "docs",
        })
    return out

def all_sources():
    return load_sources() + _linked_sources()


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def _gh_org_repos(org):
    """List public, non-fork clone URLs for a GitHub org OR user account
    (best-effort, no auth). Paginates; tries /orgs then /users."""
    import urllib.request
    last = None
    for kind in ("orgs", "users"):
        try:
            urls, page = [], 1
            while True:
                req = urllib.request.Request(
                    f"https://api.github.com/{kind}/{org}/repos?per_page=100&page={page}",
                    headers={"User-Agent": "grimoire"})
                data = json.loads(urllib.request.urlopen(req, timeout=20).read())
                if not data:
                    break
                urls += [r["clone_url"] for r in data if not r.get("fork")]
                if len(data) < 100:
                    break
                page += 1
            return urls
        except Exception as e:
            last = e
    print(f"[!] {org}: could not list repos ({last})")
    return []

def _fetch_org(name, org, dest):
    dest.mkdir(parents=True, exist_ok=True)
    repos = _gh_org_repos(org)
    print(f"[+] {name}: org {org} -> {len(repos)} repos")
    for url in repos:
        if not _safe_repo_url(url):
            print(f"[!] {name}: skipping unsafe repo URL {url!r}")
            continue
        sub = dest / Path(url).stem
        if sub.exists():
            subprocess.run(["git", "-C", str(sub), "pull", "--ff-only"], check=False)
        else:
            subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", url, str(sub)],
                           check=False)

def _fetch_pdf(name, url, dest):
    import urllib.request
    if not (isinstance(url, str) and url.startswith(("https://", "http://"))):
        print(f"[!] {name}: unsafe pdf_url scheme, skipping ({url!r})")
        return
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / Path(urllib.parse.urlparse(url).path).name
    if out.exists():
        print(f"[=] {name}: {out.name} already present")
        return
    print(f"[+] {name}: downloading {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "grimoire"})
        with urllib.request.urlopen(req, timeout=60) as r, open(out, "wb") as fh:
            fh.write(r.read())
    except Exception as e:
        print(f"[!] {name}: PDF download failed ({e})")


# --------------------------------------------------------------------------- #
# linked-document mirror
# --------------------------------------------------------------------------- #
_URL_RE = re.compile(r"https?://[^\s\]\)<>'\"`]+")
_VIDEO_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
}
_VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mp3", ".wav"}
_DOC_EXT = {".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc", ".json", ".yaml", ".yml"}


def _clean_url(url: str) -> str:
    return (url or "").rstrip(".,;:!?)\"]}'")


def _is_video_url(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return True
    host = (p.netloc or "").lower().split("@")[-1].split(":")[0]
    if host in _VIDEO_HOSTS or host.endswith(".youtube.com"):
        return True
    return Path(p.path).suffix.lower() in _VIDEO_EXT


def _iter_doc_links(base: Path, docs_dir=None, exts=None):
    for f in _walk_text_files(base, docs_dir, exts or config.TEXT_EXT):
        yield from _iter_file_links(base, f)


def _iter_file_links(base: Path, f: Path):
    try:
        text = _read_doc_text(f)
    except Exception:
        return
    rel = f.relative_to(base).as_posix()
    for raw in _URL_RE.findall(text):
        url = _clean_url(raw)
        if url and not _is_video_url(url):
            yield rel, url


def _source_units_for_links(only=None):
    selected = set(only or [])
    for s in load_sources():
        name = s["name"]
        if selected and name not in selected:
            continue
        if s.get("type") == "local":
            base = Path(s["path"]).expanduser()
        else:
            base = config.SRC_DIR / name
        if not base.exists():
            continue
        yield (s, base, s.get("docs_dir"),
               config.TEXT_EXT | {e.lower() for e in s.get("index_ext", [])})


def scan_links(only=None):
    """Return a deduped list of outbound, non-video document candidate links."""
    seen, links = set(), []
    for s, base, docs_dir, exts in _source_units_for_links(only):
        for rel, url in _iter_doc_links(base, docs_dir, exts):
            if url in seen:
                continue
            seen.add(url)
            links.append({"source": s["name"], "path": rel, "url": url})
    return links


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith(("http://", "https://")):
                self.parts.append(f" {href} ")
        elif tag in ("p", "br", "div", "section", "article", "li", "h1", "h2", "h3",
                     "h4", "h5", "h6", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("p", "div", "section", "article", "li", "h1", "h2", "h3",
                     "h4", "h5", "h6", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join((data or "").split())
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        self.parts.append(text + " ")

    def markdown(self):
        lines = []
        for line in "".join(self.parts).splitlines():
            line = " ".join(line.split())
            if line:
                lines.append(line)
        return "\n\n".join(lines)


def _slug_host(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower() or "unknown"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", host).strip("._") or "unknown"


def _url_ext(url: str, content_type: str) -> str:
    path_ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    ctype = (content_type or "").split(";", 1)[0].lower()
    if ctype == "application/pdf" or path_ext == ".pdf":
        return ".pdf"
    if path_ext in _DOC_EXT:
        return path_ext
    if ctype in ("text/html", "application/xhtml+xml"):
        return ".md"
    if ctype.startswith("text/"):
        return ".txt"
    if ctype in ("application/json", "application/yaml", "text/yaml"):
        return ".json" if "json" in ctype else ".yaml"
    return path_ext if path_ext in _DOC_EXT else ".bin"


def _download_url(url: str) -> str:
    """Prefer raw document bytes for common code-hosting document links."""
    p = urllib.parse.urlparse(url)
    host = (p.netloc or "").lower()
    parts = [x for x in p.path.split("/") if x]
    if host == "github.com" and len(parts) >= 5 and parts[2] == "blob":
        owner, repo, ref = parts[0], parts[1], parts[3]
        path = "/".join(parts[4:])
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    return url


def _read_response_limited(resp, max_bytes: int) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = resp.read(min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("too large")
    return b"".join(chunks)


def _markdown_provenance(title, url, linked_from, body):
    title = title or urllib.parse.urlparse(url).netloc or "Linked document"
    return (f"# {title}\n\n"
            f"> Mirrored from: {url}\n"
            f"> Linked from: {linked_from}\n\n"
            f"{body.strip()}\n")


def _fetch_link_doc(url: str, linked_from: str, out_dir: Path, timeout=20, max_mb=25):
    import urllib.request
    import hashlib
    max_bytes = int(max_mb * 1024 * 1024)
    fetch_url = _download_url(url)
    req = urllib.request.Request(fetch_url, headers={"User-Agent": "grimoire"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        ctype = resp.headers.get("Content-Type", "")
        clen = resp.headers.get("Content-Length")
        if clen and int(clen) > max_bytes:
            return {"url": url, "final_url": final_url, "status": "skipped-too-large"}
        data = _read_response_limited(resp, max_bytes)

    ext = _url_ext(final_url or url, ctype)
    if ext == ".bin":
        return {"url": url, "final_url": final_url, "status": "skipped-binary",
                "content_type": ctype}

    host_dir = out_dir / "docs" / _slug_host(final_url or url)
    host_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((final_url or url).encode("utf-8")).hexdigest()[:20]
    out = host_dir / f"{digest}{ext}"

    if ext == ".pdf":
        out.write_bytes(data)
    else:
        text = data.decode("utf-8", errors="ignore")
        if ext == ".md" and (ctype or "").split(";", 1)[0].lower() in ("text/html", "application/xhtml+xml"):
            parser = _HTMLText()
            parser.feed(text)
            text = _markdown_provenance(parser.title, url, linked_from, parser.markdown())
        else:
            text = _markdown_provenance("", url, linked_from, text)
        out.write_text(text, encoding="utf-8")
    return {"url": url, "final_url": final_url, "status": "fetched",
            "content_type": ctype, "path": out.relative_to(out_dir).as_posix(),
            "linked_from": linked_from}


def _queue_link(queue, queued, source, url, linked_from, depth):
    if url in queued or _is_video_url(url):
        return
    queued.add(url)
    queue.append({
        "source": source,
        "url": url,
        "linked_from": linked_from,
        "depth": depth,
    })


def _enqueue_child_links(queue, queued, source, out_dir, rec, depth_limit):
    depth = int(rec.get("depth") or 1)
    if depth >= depth_limit or not rec.get("path"):
        return
    saved = out_dir / rec["path"]
    if not saved.exists() or saved.suffix.lower() == ".pdf":
        return
    for _, child_url in _iter_file_links(out_dir, saved):
        _queue_link(queue, queued, source, child_url,
                    f"{source}-links/{rec['path']}", depth + 1)


def _write_link_manifest(manifest_path, source, meta, records, complete=False):
    manifest = {
        "name": f"{source}-links",
        "title": f"{meta.get('title', source)} linked documents",
        "category": meta.get("category", "linked"),
        "source": source,
        "complete": complete,
        "documents": sorted(records.values(), key=lambda r: r.get("url", "")),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True),
                             encoding="utf-8")


def cmd_links_scan(args):
    links = scan_links(getattr(args, "only", None))
    if getattr(args, "json", False):
        print(json.dumps({"links": links}, indent=2))
        return
    by_source = {}
    for link in links:
        by_source[link["source"]] = by_source.get(link["source"], 0) + 1
    for source, count in sorted(by_source.items()):
        print(f"{source}: {count} links")
    print(f"[=] {len(links)} non-video links found")


def cmd_links_fetch(args):
    depth_limit = max(1, int(getattr(args, "depth", 1)))
    by_source = {}
    for item in scan_links(args.only):
        by_source.setdefault(item["source"], []).append({
            "source": item["source"],
            "url": item["url"],
            "linked_from": f"{item['source']}/{item['path']}",
            "depth": 1,
        })

    source_meta = {s["name"]: s for s in load_sources()}
    config.LINK_DIR.mkdir(parents=True, exist_ok=True)
    total_fetched = total_skipped = total_failed = 0
    for source, items in sorted(by_source.items()):
        meta = source_meta.get(source, {"name": source, "category": "linked"})
        out_dir = config.LINK_DIR / f"{source}-links"
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "manifest.json"
        old = {}
        complete = False
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                complete = bool(manifest.get("complete"))
                for rec in manifest.get("documents", []):
                    old[rec.get("url")] = rec
            except Exception:
                old = {}
        if complete and not args.force:
            print(f"[=] {source}-links: complete, skipping")
            continue
        records = dict(old)
        queue = []
        queued = set()
        for item in items:
            _queue_link(queue, queued, source, item["url"], item["linked_from"], 1)
        fetched = skipped = failed = 0
        processed = 0
        print(f"[=] {source}-links: {len(queue)} seed links queued")
        try:
            while queue and (not args.limit or processed < args.limit):
                item = queue.pop(0)
                url = item["url"]
                processed += 1
                if url in records and records[url].get("status") == "fetched" and not args.force:
                    skipped += 1
                    _enqueue_child_links(queue, queued, source, out_dir, records[url], depth_limit)
                    continue
                linked_from = item["linked_from"]
                try:
                    rec = _fetch_link_doc(url, linked_from, out_dir,
                                          timeout=args.timeout, max_mb=args.max_mb)
                    rec["depth"] = item["depth"]
                    records[url] = rec
                    if rec["status"] == "fetched":
                        fetched += 1
                        _enqueue_child_links(queue, queued, source, out_dir, rec, depth_limit)
                    else:
                        skipped += 1
                except Exception as e:
                    records[url] = {"url": url, "status": "failed", "error": str(e),
                                    "linked_from": linked_from, "depth": item["depth"]}
                    failed += 1
                if processed % 25 == 0:
                    _write_link_manifest(manifest_path, source, meta, records)
                    print(f"[=] {source}-links: {processed} processed, "
                          f"{fetched} fetched, {skipped} skipped, {failed} failed, "
                          f"{len(queue)} queued")
        finally:
            _write_link_manifest(manifest_path, source, meta, records)
        _write_link_manifest(manifest_path, source, meta, records, complete=True)
        total_fetched += fetched
        total_skipped += skipped
        total_failed += failed
        print(f"[+] {source}-links: {fetched} fetched, {skipped} skipped, "
              f"{failed} failed -> {out_dir}")
    print(f"[=] links fetch done: {total_fetched} fetched, {total_skipped} skipped, "
          f"{total_failed} failed")

def cmd_fetch(args):
    config.SRC_DIR.mkdir(parents=True, exist_ok=True)
    sources = load_sources()
    only = set(args.only or [])
    for s in sources:
        name = s["name"]
        if only and name not in only:
            continue
        if s.get("type") == "local":
            print(f"[=] {name}: local source, skipping clone")
            continue
        dest = config.SRC_DIR / name
        if s.get("org"):
            _fetch_org(name, s["org"], dest)
            continue
        if s.get("pdf_url"):
            _fetch_pdf(name, s["pdf_url"], dest)
            continue
        repo = s.get("repo")
        if not repo:
            print(f"[!] {name}: no repo URL, skipping")
            continue
        if not _safe_repo_url(repo):
            print(f"[!] {name}: unsafe repo URL scheme, skipping ({repo!r})")
            continue
        sparse = s.get("sparse")  # list of paths to check out (e.g. ["doc"]) for huge repos
        if dest.exists():
            print(f"[~] {name}: updating")
            if sparse:
                subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", *sparse],
                               check=False)
            subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"], check=False)
        elif sparse:
            # Blobless + sparse + shallow: fetch only the doc subtree of a large repo.
            print(f"[+] {name}: sparse cloning {repo} (paths: {', '.join(sparse)})")
            if subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none",
                               "--sparse", repo, str(dest)], check=False).returncode == 0:
                subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", *sparse],
                               check=False)
        else:
            print(f"[+] {name}: cloning {repo}")
            subprocess.run(["git", "clone", "--depth", "1", repo, str(dest)], check=False)
    print("[=] fetch done")


# --------------------------------------------------------------------------- #
# build (optional native builders; search works without it)
# --------------------------------------------------------------------------- #
def _have(tool):
    return subprocess.run(["bash", "-lc", f"command -v {tool}"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def cmd_build(args):
    config.BUILD_DIR.mkdir(parents=True, exist_ok=True)
    for s in load_sources():
        name, kind = s["name"], s.get("build", "markdown")
        src = config.SRC_DIR / name
        if not src.exists():
            continue
        out = config.BUILD_DIR / name
        if kind == "mdbook" and _have("mdbook"):
            print(f"[+] {name}: mdbook build")
            subprocess.run(["mdbook", "build", "-d", str(out)], cwd=src, check=False)
        elif kind == "mkdocs" and _have("mkdocs"):
            print(f"[+] {name}: mkdocs build")
            subprocess.run(["mkdocs", "build", "-d", str(out)], cwd=src, check=False)
        else:
            # jekyll/hugo/markdown or builder missing: search uses raw markdown,
            # so a native build is not required to use the tool.
            print(f"[=] {name}: no native build ({kind}); markdown indexed directly")
    print("[=] build done")


# --------------------------------------------------------------------------- #
# text extraction
# --------------------------------------------------------------------------- #
def _title_of(path: Path, text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or path.stem
        if line.startswith("title:"):  # yaml/front-matter
            return line.split(":", 1)[1].strip().strip('"\'') or path.stem
    return path.stem.replace("-", " ").replace("_", " ")

def _walk_text_files(base: Path, docs_dir=None, exts=None):
    exts = exts or config.TEXT_EXT
    root = base / docs_dir if docs_dir else base
    if not root.exists():
        root = base
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in config.IGNORE_DIRS]
        for fn in filenames:
            if Path(fn).suffix.lower() in exts:
                yield Path(dirpath) / fn

def _pdf_text(path: Path) -> str:
    """Extract text from a PDF (books like RE-for-Beginners) via poppler's
    pdftotext, if installed. Returns '' otherwise (PDF kept but not indexed)."""
    import shutil
    if not shutil.which("pdftotext"):
        return ""
    r = subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""

def _notebook_to_markdown(text: str) -> str:
    """Convert a Jupyter .ipynb (JSON) into readable markdown: markdown cells
    verbatim, code cells as fenced blocks, text outputs as plain output blocks.
    Falls back to the raw text if it is not valid notebook JSON."""
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

def _yaml_humanize(text: str) -> str:
    """Re-render YAML so escaped-unicode scalars (e.g. "S\\xE9curit\\xE9",
    "\\u2014") show as real characters. Many machine-generated framework files
    (CISO Assistant, etc.) store non-ASCII this way, which is unreadable in the
    viewer and unsearchable. Falls back to the raw text if it does not parse
    cleanly or PyYAML is unavailable.

    Only files that actually contain escaped-unicode sequences are re-emitted;
    clean YAML is returned verbatim so its comments and formatting are kept (a
    re-dump would drop comments)."""
    import re as _re
    if not _re.search(r"\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}", text):
        return text
    try:
        import yaml
        docs = list(yaml.safe_load_all(text))   # handle multi-document files too
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

def _read_doc_text(f: Path) -> str:
    """Read a doc file as text, normalizing on the way: notebooks -> markdown,
    YAML -> unicode-decoded YAML (so escaped accents/dashes are readable)."""
    text = f.read_text(encoding="utf-8", errors="ignore")
    suf = f.suffix.lower()
    if suf == ".ipynb":
        return _notebook_to_markdown(text)
    if suf in (".yml", ".yaml"):
        return _yaml_humanize(text)
    return text


# --------------------------------------------------------------------------- #
# index store (all SQL lives here; every statement is parameterized)
# --------------------------------------------------------------------------- #
def _embedding_tokens(text: str):
    """Tokenize text for the built-in local embedding baseline.

    This is intentionally simple and deterministic. Real semantic search should
    set GRIMOIRE_EMBED_COMMAND to a local model command; the hash embedder exists
    so the vector index is usable and testable without a network/model download.
    """
    words = re.findall(r"[A-Za-z0-9_]{2,}", (text or "").lower())
    for w in words:
        yield w
    for a, b in zip(words, words[1:]):
        yield f"{a}_{b}"


def _hash_embedding(text: str, dim=None):
    dim = int(dim or config.VECTOR_DIM)
    vec = [0.0] * dim
    for tok in _embedding_tokens(text):
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        n = int.from_bytes(digest, "little")
        idx = n % dim
        sign = -1.0 if (n >> 63) else 1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _embedder_name():
    return "command" if config.EMBED_COMMAND else "hash-v1"


def _embed_with_command(text: str):
    """Run a local embedding command.

    The command reads UTF-8 text on stdin and must print a JSON array of floats.
    This keeps Grimoire offline/local while letting operators choose their own
    model runtime (llama.cpp wrapper, sentence-transformers script, qmd bridge,
    etc.) without binding the core package to a heavyweight ML dependency.
    """
    cmd = shlex.split(config.EMBED_COMMAND or "")
    if not cmd:
        raise ValueError("GRIMOIRE_EMBED_COMMAND is empty")
    p = subprocess.run(
        cmd,
        input=text,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "embedding command failed").strip())
    vec = json.loads(p.stdout)
    if not isinstance(vec, list) or not vec:
        raise ValueError("embedding command must return a non-empty JSON list")
    return [float(x) for x in vec]


def _embed_text(text: str):
    vec = _embed_with_command(text) if config.EMBED_COMMAND else _hash_embedding(text)
    if len(vec) != int(config.VECTOR_DIM):
        raise ValueError(
            f"embedding dimension {len(vec)} != GRIMOIRE_VECTOR_DIM={config.VECTOR_DIM}"
        )
    return vec


def _sqlite_vec():
    try:
        import sqlite_vec
        return sqlite_vec
    except Exception:
        return None


def _rrf(rank: int, k=60):
    return 1.0 / (k + rank)


def _plain_snippet(body: str, raw: str, width=260):
    """Snippet fallback for vector-only hits where FTS5 snippet() is unavailable."""
    body = " ".join((body or "").split())
    if not body:
        return ""
    toks = [t.lower() for t in re.findall(r"[A-Za-z0-9_]{2,}", raw or "")]
    lower = body.lower()
    pos = -1
    for tok in toks:
        pos = lower.find(tok)
        if pos >= 0:
            break
    if pos < 0:
        return body[:width]
    start = max(0, pos - width // 3)
    end = min(len(body), start + width)
    prefix = "..." if start else ""
    suffix = "..." if end < len(body) else ""
    return prefix + body[start:end] + suffix


class Index:
    """A thin, parameterized wrapper around the SQLite FTS5 index. Centralizing
    every query here is the 'nice method' for injection safety: callers pass
    values, never SQL, and there is exactly one place to audit."""
    SCHEMA = ("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5("
              "source, title, category, relpath, body, "
              "tokenize='porter unicode61')")
    COLUMNS = ("source", "title", "category", "relpath", "body")

    def __init__(self, path=None, vectors=True):
        self.db = sqlite3.connect(str(path or config.INDEX_DB))
        self.vector_enabled = False
        self.vector_error = None
        self._sqlite_vec = None
        self._vector_warned = False
        self._vectors_requested = vectors

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def create(self):
        try:
            self.db.execute(self.SCHEMA)
        except sqlite3.OperationalError as e:
            sys.exit(f"[!] SQLite FTS5 not available in this Python build: {e}")
        if self._vectors_requested:
            self.create_vectors()

    def create_vectors(self):
        mod = _sqlite_vec()
        if mod is None:
            self.vector_error = "sqlite-vec not installed"
            return False
        try:
            self.db.enable_load_extension(True)
            mod.load(self.db)
        except Exception as e:
            self.vector_error = f"sqlite-vec load failed: {e}"
            return False
        finally:
            try:
                self.db.enable_load_extension(False)
            except Exception:
                pass
        self._sqlite_vec = mod
        dim = int(config.VECTOR_DIM)
        embedder = _embedder_name()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS vector_meta("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        meta = dict(self.db.execute("SELECT key, value FROM vector_meta").fetchall())
        existing = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'doc_vectors'"
        ).fetchone()
        if existing and (
            meta.get("dim") not in (None, str(dim)) or
            meta.get("embedder") not in (None, embedder)
        ):
            self.db.execute("DROP TABLE doc_vectors")
            existing = None
        if not existing:
            self.db.execute(
                "CREATE VIRTUAL TABLE doc_vectors USING vec0("
                f"embedding float[{dim}], source text, category text)"
            )
        self.db.execute(
            "INSERT OR REPLACE INTO vector_meta(key, value) VALUES "
            "('dim', ?), ('embedder', ?)",
            (str(dim), embedder),
        )
        self.vector_enabled = True
        return True

    def delete_source(self, name):
        self.db.execute("DELETE FROM docs WHERE source = ?", (name,))
        if self.vector_enabled:
            self.db.execute("DELETE FROM doc_vectors WHERE source = ?", (name,))

    def insert(self, source, title, category, relpath, body):
        cur = self.db.execute(
            "INSERT INTO docs(source, title, category, relpath, body) "
            "VALUES (?, ?, ?, ?, ?)", (source, title, category, relpath, body))
        rowid = cur.lastrowid
        if self.vector_enabled:
            self.insert_vector(rowid, source, category, f"{title}\n{relpath}\n{body}")
        return rowid

    def insert_vector(self, rowid, source, category, text):
        try:
            vec = _embed_text(text)
            blob = self._sqlite_vec.serialize_float32(vec)
            self.db.execute(
                "INSERT INTO doc_vectors(rowid, embedding, source, category) "
                "VALUES (?, ?, ?, ?)",
                (int(rowid), blob, source, category),
            )
        except Exception as e:
            # Do not break the offline lexical index because an optional embedder
            # failed. The index status command reports this state.
            self.vector_error = str(e)
            if not self._vector_warned:
                print(f"[!] vector indexing disabled for this run: {e}")
                self._vector_warned = True
            self.vector_enabled = False

    def has_rows(self, name) -> bool:
        return self.db.execute(
            "SELECT 1 FROM docs WHERE source = ? LIMIT 1", (name,)).fetchone() is not None

    def distinct_sources(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT source FROM docs")]

    def count(self) -> int:
        return self.db.execute("SELECT count(*) FROM docs").fetchone()[0]

    def vector_count(self) -> int:
        if not self.vector_enabled:
            return 0
        try:
            return self.db.execute("SELECT count(*) FROM doc_vectors").fetchone()[0]
        except sqlite3.OperationalError:
            return 0

    def has_vector_rows(self, name) -> bool:
        if not self.vector_enabled:
            return True
        try:
            return self.db.execute(
                "SELECT 1 FROM doc_vectors WHERE source = ? LIMIT 1", (name,)
            ).fetchone() is not None
        except sqlite3.OperationalError:
            return False

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.close()

    def _lexical_hits(self, raw, cat=None, limit=60):
        """Ranked full-text search. `raw` is sanitized by _fts_query; `cat` and
        `limit` are bound as parameters. Returns rows:
        (source, title, category, relpath, snippet-with-mark-sentinels)."""
        match = _fts_query((raw or "").strip())
        if not match:
            return []
        sql = ("SELECT rowid, source, title, category, relpath, "
               "snippet(docs, 4, char(2), char(3), ' ... ', 12) "
               "FROM docs WHERE docs MATCH ? ")
        params = [match]
        if cat:
            sql += "AND category = ? "
            params.append(cat)
        sql += "ORDER BY bm25(docs) LIMIT ?"
        params.append(int(limit))
        try:
            return self.db.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

    def _vector_hit_ids(self, raw, cat=None, limit=60):
        if not self.vector_enabled:
            return []
        try:
            vec = _embed_text(raw)
            blob = self._sqlite_vec.serialize_float32(vec)
            # Fetch a wider pool, then apply the category filter through docs.
            # This avoids relying on sqlite-vec metadata-filter planner details.
            pool = max(int(limit) * (4 if cat else 1), int(limit))
            rows = self.db.execute(
                "SELECT rowid, distance FROM doc_vectors "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (blob, pool),
            ).fetchall()
        except Exception as e:
            self.vector_error = str(e)
            return []
        if not cat:
            return [(int(r[0]), float(r[1])) for r in rows]
        out = []
        for rowid, distance in rows:
            got = self.db.execute(
                "SELECT 1 FROM docs WHERE rowid = ? AND category = ?",
                (int(rowid), cat),
            ).fetchone()
            if got:
                out.append((int(rowid), float(distance)))
                if len(out) >= limit:
                    break
        return out

    def _rows_by_id(self, ids, raw):
        if not ids:
            return {}
        clean_ids = [int(x) for x in ids]
        placeholders = ",".join("?" for _ in clean_ids)
        rows = self.db.execute(
            f"SELECT rowid, source, title, category, relpath, body "
            f"FROM docs WHERE rowid IN ({placeholders})",
            clean_ids,
        ).fetchall()
        return {
            int(rowid): (source, title, category, relpath, _plain_snippet(body, raw))
            for rowid, source, title, category, relpath, body in rows
        }

    def search(self, raw, cat=None, limit=60, hybrid=True):
        """Ranked search, using BM25 plus optional sqlite-vec candidates.

        The return shape remains the legacy tuple:
        (source, title, category, relpath, snippet-with-mark-sentinels).
        """
        limit = int(limit)
        pool = max(limit * 4, 60)
        lexical = self._lexical_hits(raw, cat, pool)
        if not hybrid:
            return [r[1:] for r in lexical[:limit]]

        vector = self._vector_hit_ids(raw, cat, pool)
        if not vector:
            return [r[1:] for r in lexical[:limit]]

        scores = {}
        rowdata = {}
        for rank, row in enumerate(lexical, 1):
            rowid = int(row[0])
            scores[rowid] = scores.get(rowid, 0.0) + _rrf(rank)
            rowdata[rowid] = row[1:]
        for rank, (rowid, _distance) in enumerate(vector, 1):
            scores[rowid] = scores.get(rowid, 0.0) + _rrf(rank)

        missing = [rowid for rowid in scores if rowid not in rowdata]
        rowdata.update(self._rows_by_id(missing, raw))
        ordered = sorted(scores, key=lambda r: (-scores[r], r))
        return [rowdata[rowid] for rowid in ordered if rowid in rowdata][:limit]


def _fts_query(raw: str) -> str:
    # Build a safe FTS5 MATCH expression: quote each token, prefix-match.
    toks = [t for t in "".join(c if c.isalnum() else " " for c in raw).split() if t]
    return " ".join(f'"{t}"*' for t in toks)

def search(raw, cat=None, limit=60, hybrid=True):
    """Convenience: open the default index, run a parameterized search, close."""
    with Index() as idx:
        idx.create_vectors()
        return idx.search(raw, cat, limit, hybrid=hybrid)


def vector_status():
    with Index() as idx:
        ok = idx.create_vectors()
        return {
            "enabled": bool(ok),
            "error": idx.vector_error,
            "dim": int(config.VECTOR_DIM),
            "embedder": _embedder_name(),
            "vectors": idx.vector_count(),
            "sqlite_vec": bool(idx._sqlite_vec),
        }


# --------------------------------------------------------------------------- #
# indexing operations
# --------------------------------------------------------------------------- #
def _source_rev(base: Path, docs_dir=None, exts=None) -> str:
    """A revision token for a source: the git commit if it's a checkout, else a
    hash of the (path, mtime, size) of its text files. Used to skip unchanged
    sources on reindex."""
    exts = exts or config.TEXT_EXT
    if (base / ".git").exists():
        r = subprocess.run(["git", "-C", str(base), "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return "git:" + r.stdout.strip()
    import hashlib
    h = hashlib.sha1()
    for f in sorted(_walk_text_files(base, docs_dir, exts | {".pdf"})):
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{f}:{int(st.st_mtime)}:{st.st_size}\n".encode())
    return "hash:" + h.hexdigest()

def _index_source(idx: Index, name, cat, base, docs_dir=None, exts=None) -> int:
    exts = exts or config.TEXT_EXT
    idx.delete_source(name)
    cnt = 0
    for f in _walk_text_files(base, docs_dir, exts):
        try:
            text = _read_doc_text(f)
        except Exception:
            continue
        idx.insert(name, _title_of(f, text), cat, f.relative_to(base).as_posix(), text)
        cnt += 1
    # PDFs (books) - extracted to text when pdftotext is available
    for f in _walk_text_files(base, docs_dir, {".pdf"}):
        text = _pdf_text(f)
        if text.strip():
            idx.insert(name, f.stem.replace("-", " "), cat,
                       f.relative_to(base).as_posix(), text)
            cnt += 1
    return cnt

def cmd_index(args):
    config.DATA.mkdir(parents=True, exist_ok=True)
    full = getattr(args, "force", False)
    if full and config.INDEX_DB.exists():
        config.INDEX_DB.unlink()
    state = {}
    if not full and config.INDEX_STATE.exists():
        try:
            state = json.loads(config.INDEX_STATE.read_text())
        except Exception:
            state = {}

    idx = Index()
    idx.create()
    try:
        new_state, reindexed, skipped = {}, 0, 0
        units = [(s["name"],
                  s.get("category", "other"),
                  Path(s["path"]).expanduser() if s.get("type") == "local"
                  else config.SRC_DIR / s["name"],
                  s.get("docs_dir"),
                  config.TEXT_EXT | {e.lower() for e in s.get("index_ext", [])})
                 for s in load_sources()]
        units += [(s["name"], s.get("category", "linked"), Path(s["path"]),
                   s.get("docs_dir"), config.TEXT_EXT | {".txt", ".json", ".adoc"})
                  for s in _linked_sources()]
        if config.CUSTOM_DIR.exists():
            units.append(("custom", "custom", config.CUSTOM_DIR, None, config.TEXT_EXT))

        for name, cat, base, docs_dir, exts in units:
            if not base.exists():
                continue
            rev = _source_rev(base, docs_dir, exts)
            new_state[name] = rev
            if (not full and state.get(name) == rev and idx.has_rows(name)
                    and idx.has_vector_rows(name)):
                print(f"[=] {name}: unchanged, skipping")
                skipped += 1
                continue
            cnt = _index_source(idx, name, cat, base, docs_dir, exts)
            print(f"[+] {name}: indexed {cnt} docs")
            reindexed += 1

        # prune sources that are gone from the manifest/custom
        present = set(new_state)
        for src in idx.distinct_sources():
            if src not in present:
                idx.delete_source(src)
                print(f"[-] {src}: removed (no longer a source)")
        idx.commit()
        total = idx.count()
        vectors = idx.vector_count()
        vector_error = idx.vector_error
    finally:
        idx.close()
    config.INDEX_STATE.write_text(json.dumps(new_state, indent=0))
    print(f"[=] index done: {reindexed} reindexed, {skipped} unchanged, "
          f"{total} docs total -> {config.INDEX_DB}")
    if vectors:
        print(f"[=] vector index: {vectors} embeddings ({_embedder_name()}, "
              f"{config.VECTOR_DIM} dims)")
    elif vector_error:
        print(f"[=] vector index disabled: {vector_error}")


def _path_size(p: Path) -> int:
    """Bytes used by a file or (recursively) a directory; best-effort."""
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def cmd_clean(args):
    """Remove generated data so a later fetch/index rebuilds it from scratch.

    By default only the search index (index.db + its incremental state) is
    dropped. ``--sources`` also removes the cloned repos and native builds;
    ``--all`` wipes the whole data/ dir. The manifest (sources.yaml) and your
    own custom/ docs are never touched."""
    import shutil
    wipe_all = getattr(args, "all", False)
    drop_sources = wipe_all or getattr(args, "sources", False)

    if wipe_all:
        targets = [config.DATA]
    else:
        targets = [config.INDEX_DB, config.INDEX_STATE]
        if drop_sources:
            targets += [config.SRC_DIR, config.BUILD_DIR, config.LINK_DIR]

    existing = [t for t in targets if t.exists()]
    if not existing:
        print("[=] nothing to clean (no generated data found)")
        return

    freed = 0
    for t in existing:
        size = _path_size(t)
        freed += size
        if t.is_dir():
            shutil.rmtree(t, ignore_errors=True)
        else:
            try:
                t.unlink()
            except OSError as e:
                print(f"[!] could not remove {t}: {e}")
                continue
        print(f"[-] removed {t} ({_human(size)})")
    rebuild = "all" if drop_sources else "index"
    print(f"[=] cleaned {_human(freed)} - run `grimoire {rebuild}` to rebuild")


# --------------------------------------------------------------------------- #
# doc resolution (path-traversal guarded)
# --------------------------------------------------------------------------- #
def _resolve_doc(source: str, relpath: str):
    if source == "custom":
        base = config.CUSTOM_DIR
    else:
        for s in all_sources():
            if s["name"] == source:
                base = (Path(s["path"]).expanduser() if s.get("type") == "local"
                        else Path(s["path"]) if s.get("type") == "linked"
                        else config.SRC_DIR / source)
                break
        else:
            return None
    try:
        target = (base / relpath).resolve()  # .resolve() also collapses symlink escapes
        if base.resolve() in target.parents or target == base.resolve():
            return target if target.is_file() else None
    except (OSError, ValueError):
        return None  # malformed path (e.g. embedded NUL)
    return None  # path traversal guard

def categories():
    """Category -> [{name, title}] for the filter chips."""
    cats = {}
    for s in all_sources():
        cats.setdefault(s.get("category", "other"), []).append(
            {"name": s["name"], "title": s.get("title", s["name"])})
    return cats

def source_meta(name: str):
    for s in all_sources():
        if s["name"] == name:
            return s
    return None

def doc_text(source: str, relpath: str):
    """Return a doc's content as text (notebook -> markdown, pdf -> extracted
    text), path-traversal guarded. None if it does not resolve. Used by the MCP
    server so an attached model can read full documents, not just snippets."""
    f = _resolve_doc(source, relpath)
    if not f or f.suffix.lower() not in config.DOC_EXT:
        return None
    if f.suffix.lower() == ".pdf":
        return _pdf_text(f)
    try:
        return _read_doc_text(f)
    except OSError:
        return None
