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
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse
from pathlib import Path

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
class Index:
    """A thin, parameterized wrapper around the SQLite FTS5 index. Centralizing
    every query here is the 'nice method' for injection safety: callers pass
    values, never SQL, and there is exactly one place to audit."""
    SCHEMA = ("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5("
              "source, title, category, relpath, body, "
              "tokenize='porter unicode61')")
    COLUMNS = ("source", "title", "category", "relpath", "body")

    def __init__(self, path=None):
        self.db = sqlite3.connect(str(path or config.INDEX_DB))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def create(self):
        try:
            self.db.execute(self.SCHEMA)
        except sqlite3.OperationalError as e:
            sys.exit(f"[!] SQLite FTS5 not available in this Python build: {e}")

    def delete_source(self, name):
        self.db.execute("DELETE FROM docs WHERE source = ?", (name,))

    def insert(self, source, title, category, relpath, body):
        self.db.execute(
            "INSERT INTO docs(source, title, category, relpath, body) "
            "VALUES (?, ?, ?, ?, ?)", (source, title, category, relpath, body))

    def has_rows(self, name) -> bool:
        return self.db.execute(
            "SELECT 1 FROM docs WHERE source = ? LIMIT 1", (name,)).fetchone() is not None

    def distinct_sources(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT source FROM docs")]

    def count(self) -> int:
        return self.db.execute("SELECT count(*) FROM docs").fetchone()[0]

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.close()

    def search(self, raw, cat=None, limit=60):
        """Ranked full-text search. `raw` is sanitized by _fts_query; `cat` and
        `limit` are bound as parameters. Returns rows:
        (source, title, category, relpath, snippet-with-mark-sentinels)."""
        match = _fts_query((raw or "").strip())
        if not match:
            return []
        sql = ("SELECT source, title, category, relpath, "
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


def _fts_query(raw: str) -> str:
    # Build a safe FTS5 MATCH expression: quote each token, prefix-match.
    toks = [t for t in "".join(c if c.isalnum() else " " for c in raw).split() if t]
    return " ".join(f'"{t}"*' for t in toks)

def search(raw, cat=None, limit=60):
    """Convenience: open the default index, run a parameterized search, close."""
    with Index() as idx:
        return idx.search(raw, cat, limit)


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
        if config.CUSTOM_DIR.exists():
            units.append(("custom", "custom", config.CUSTOM_DIR, None, config.TEXT_EXT))

        for name, cat, base, docs_dir, exts in units:
            if not base.exists():
                continue
            rev = _source_rev(base, docs_dir, exts)
            new_state[name] = rev
            if not full and state.get(name) == rev and idx.has_rows(name):
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
    finally:
        idx.close()
    config.INDEX_STATE.write_text(json.dumps(new_state, indent=0))
    print(f"[=] index done: {reindexed} reindexed, {skipped} unchanged, "
          f"{total} docs total -> {config.INDEX_DB}")


# --------------------------------------------------------------------------- #
# doc resolution (path-traversal guarded)
# --------------------------------------------------------------------------- #
def _resolve_doc(source: str, relpath: str):
    if source == "custom":
        base = config.CUSTOM_DIR
    else:
        for s in load_sources():
            if s["name"] == source:
                base = (Path(s["path"]).expanduser() if s.get("type") == "local"
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
    for s in load_sources():
        cats.setdefault(s.get("category", "other"), []).append(
            {"name": s["name"], "title": s.get("title", s["name"])})
    return cats

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
