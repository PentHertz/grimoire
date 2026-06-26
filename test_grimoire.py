#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
"""
Unit + security tests for Grimoire. Stdlib only (unittest); no network and no
markdown dependency required.

Run:  cd grimoire && python3 -m unittest -v
"""
import json
import tempfile
import threading
import types
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

from grimoire_app import config, model, view, controller, mcp, runner

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
       b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


# --------------------------------------------------------------------------- #
# Pure-function unit tests (no globals, no server)
# --------------------------------------------------------------------------- #
class TestPure(unittest.TestCase):
    def test_fts_query_quotes_and_prefixes(self):
        self.assertEqual(model._fts_query("sql ssrf"), '"sql"* "ssrf"*')

    def test_fts_query_strips_operators(self):
        # FTS5/SQL metacharacters must not survive into the MATCH expression
        out = model._fts_query('a" OR "1"="1 ; DROP--')
        self.assertNotIn('"="', out)
        self.assertNotIn(";", out)
        for tok in out.split():
            self.assertTrue(tok.startswith('"') and tok.endswith('*'))

    def test_title_from_heading_and_frontmatter(self):
        self.assertEqual(model._title_of(Path("x.md"), "# Hello\nbody"), "Hello")
        self.assertEqual(model._title_of(Path("x.md"), "title: My Doc\n"), "My Doc")
        self.assertEqual(model._title_of(Path("foo-bar.md"), "no heading"), "foo bar")

    def test_obsidian_preprocess(self):
        out = view._obsidian_preprocess(
            "---\ntitle: x\n---\n# Heading\n[[Gopher]] [[SSRF|alias]] #ssrf")
        self.assertNotIn("title: x", out)                 # frontmatter stripped
        self.assertIn("# Heading", out)                   # heading NOT a tag
        self.assertIn('/?q=Gopher', out)                  # wikilink -> search
        self.assertIn(">alias</a>", out)                  # alias label
        self.assertIn('class="tag"', out)                 # #tag styled

    def test_notebook_to_markdown(self):
        nb = json.dumps({
            "metadata": {"kernelspec": {"language": "python"}},
            "cells": [
                {"cell_type": "markdown",
                 "source": ["# EMFI Notebook\n", "Inject faults with the PicoEMP.\n"]},
                {"cell_type": "code", "source": ["scope.glitch.repeat = 5\n"],
                 "outputs": [{"output_type": "stream", "text": ["glitch ok\n"]}]},
            ],
        })
        out = model._notebook_to_markdown(nb)
        self.assertIn("# EMFI Notebook", out)              # markdown cell verbatim
        self.assertIn("```python\nscope.glitch.repeat = 5\n```", out)  # code fenced
        self.assertIn("glitch ok", out)                    # text output kept
        self.assertNotIn("cell_type", out)                 # no raw JSON leaks
        self.assertNotIn('"source"', out)
        # malformed JSON falls back to the raw text (never raises)
        self.assertEqual(model._notebook_to_markdown("not json {"), "not json {")

    def test_pdf_to_html_pages_and_escapes(self):
        out = view._pdf_to_html("Intro <b>x</b>\ntext\fpage two body")
        self.assertEqual(out.count("pdfpage"), 2)            # split on form-feed
        self.assertIn("page 1", out)
        self.assertIn("page 2", out)
        self.assertIn("&lt;b&gt;", out)                      # PDF text is escaped
        self.assertNotIn("<b>x</b>", out)
        self.assertIn("(no extractable text", view._pdf_to_html("\f  \f"))  # empty -> note

    def test_yaml_humanize_decodes_escaped_unicode(self):
        # framework YAMLs store non-ASCII as \xE9 / \xA9 escapes -> decode them
        raw = 'name: "S\\xE9curit\\xE9"\ncopyright: "\\xA9 2026"\n'
        out = model._yaml_humanize(raw)
        self.assertIn("S\u00e9curit\u00e9", out)  # real e-acute, not \xE9
        self.assertIn("\u00a9 2026", out)            # real copyright sign
        self.assertNotIn("\\xE9", out)
        # unparseable YAML falls back to the raw text (never raises)
        self.assertEqual(model._yaml_humanize("{ unclosed"), "{ unclosed")

    # ----- exec layer: guard (denylist + scope) -------------------------------- #
    def test_runner_guard_denylist(self):
        for bad in ("rm -rf /", "rm -rf /*", "mkfs.ext4 /dev/sda", ":(){ :|:& };:",
                    "shutdown -h now"):
            self.assertIsNotNone(runner.guard(bad))      # refused in all modes
        self.assertIsNone(runner.guard("nmap -sV 10.0.0.5"))   # benign, no scope

    def test_runner_guard_scope(self):
        scope = ["10.0.0.0/24", "app.example.test"]
        self.assertIsNone(runner.guard("nmap 10.0.0.5", scope))        # in CIDR
        self.assertIsNone(runner.guard("curl http://app.example.test/", scope))
        self.assertIsNone(runner.guard("nmap localhost", scope))       # ignored host
        self.assertIsNotNone(runner.guard("nmap 8.8.8.8", scope))      # out of scope
        self.assertIsNotNone(runner.guard("curl https://evil.example.org", scope))

    def test_runner_run_executes_and_truncates(self):
        r = runner.run("echo grimoire_exec_ok")
        self.assertEqual(r["rc"], 0)
        self.assertIn("grimoire_exec_ok", r["stdout"])
        self.assertIn("scope", r["warning"])             # warns when no scope set
        # a denied command is refused WITHOUT executing
        self.assertTrue(runner.run("rm -rf /")["refused"])

    def test_runner_plan_install_structure(self):
        plan = runner.plan_install("bash")               # present -> noop step
        self.assertTrue(plan["present"])
        self.assertEqual(plan["steps"][0]["kind"], "noop")
        env = runner.detect_env()
        for k in ("in_rfswift", "os_id", "pkg_manager", "is_root"):
            self.assertIn(k, env)

    def test_runner_rejects_tool_name_injection(self):
        # a prompt-injected tool name must NEVER reach a shell command
        self.assertFalse(runner.valid_tool_name("x; touch /tmp/grimoire_PWNED"))
        self.assertFalse(runner.valid_tool_name("$(id)"))
        self.assertFalse(runner.valid_tool_name("a b"))
        self.assertFalse(runner.valid_tool_name("a`b`"))
        self.assertTrue(runner.valid_tool_name("python3-pip"))
        self.assertTrue(runner.valid_tool_name("nmap"))
        plan = runner.plan_install("x; touch /tmp/grimoire_PWNED; echo")
        self.assertTrue(all("touch" not in (s.get("command") or "") for s in plan["steps"]))
        res = runner.install("x; touch /tmp/grimoire_PWNED; echo")
        self.assertFalse(res.get("installed", True))
        self.assertFalse(Path("/tmp/grimoire_PWNED").exists())

    def test_runner_scope_ipv6(self):
        self.assertIsNotNone(runner.guard("ping6 2001:4860:4860::8888", ["10.0.0.0/24"]))
        self.assertIsNone(runner.guard("ping6 2001:db8::5", ["2001:db8::/32"]))

    def test_safe_repo_url_blocks_transport_helpers(self):
        for u in ("https://github.com/x/y", "http://h/x", "git://h/x",
                  "ssh://h/x", "git@github.com:x/y.git"):
            self.assertTrue(model._safe_repo_url(u), u)
        for u in ("ext::sh -c id", "file:///etc/passwd", "fd::17",
                  "--upload-pack=x", "-x", "", None, "javascript:alert(1)"):
            self.assertFalse(model._safe_repo_url(u), u)

    def test_rewrite_assets(self):
        body = ('<img src="pics/a.png"><img src="/repo/b.png">'
                '<img src="https://x/c.png"><a href="other.md">n</a>'
                '<a href="https://x.com">e</a>')
        out = view._rewrite_assets(body, "src1", "docs/page.md")
        self.assertIn("/asset?src=src1&path=docs/pics/a.png", out)   # relative
        self.assertIn("/asset?src=src1&path=repo/b.png", out)        # root-absolute
        self.assertIn('src="https://x/c.png"', out)                  # external img kept
        self.assertIn("/doc?src=src1&path=docs/other.md", out)       # md link -> /doc
        self.assertIn('href="https://x.com"', out)                   # external link kept


# --------------------------------------------------------------------------- #
# Integration + security tests (temp data dir + in-process server)
# --------------------------------------------------------------------------- #
class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="grimoire-test-"))
        # repoint module globals at the temp sandbox
        config.DATA = self.tmp / "data"
        config.SRC_DIR = config.DATA / "sources"
        config.BUILD_DIR = config.DATA / "build"
        config.INDEX_DB = config.DATA / "index.db"
        config.INDEX_STATE = config.DATA / "index_state.json"
        config.CUSTOM_DIR = self.tmp / "custom"
        config.SOURCES_FILE = self.tmp / "sources.yaml"
        config.SRC_DIR.mkdir(parents=True)
        config.CUSTOM_DIR.mkdir(parents=True)

        src = self.tmp / "docsrc"
        (src / "pics").mkdir(parents=True)
        (src / "sqli.md").write_text("# SQLi\nUNION SELECT magicword from users")
        # a poisoned doc with an HTML/JS payload + a searchable token
        (src / "evil.md").write_text(
            "# Evil\n<script>alert(1)</script><img src=x onerror=alert(2)> magicword")
        # template-injection probes: must NOT be evaluated (no template engine)
        (src / "ssti.md").write_text(
            "# {{1337*7}}\nzztoken {{1337*7}} ${1337*7} {src} %(x)s [[1337*7]]")
        # a Jupyter notebook: must index as readable markdown, not raw JSON
        (src / "nb.ipynb").write_text(json.dumps({
            "cells": [
                {"cell_type": "markdown", "source": ["# Glitch NB\n", "nbmagicword\n"]},
                {"cell_type": "code", "source": ["fault_inject()\n"], "outputs": []},
            ]}))
        # RPISEC MBE-style lab notes use a .readme suffix, not .md.
        (src / "lab6B.readme").write_text("# MBE lab\nret2libc readmetoken")
        (src / "pics" / "a.png").write_bytes(PNG)
        (src / "secret.env").write_text("API_KEY=supersecret")     # must NOT be servable
        try:
            (src / "passwd-link").symlink_to("/etc/passwd")        # symlink escape probe
        except (OSError, NotImplementedError):
            pass
        self.src = src
        config.SOURCES_FILE.write_text(
            "sources:\n"
            f"  - {{name: loc, title: Loc, type: local, path: {src}, "
            "category: web-api, index_ext: [.ipynb, .readme]}\n")
        model.cmd_index(types.SimpleNamespace(force=True))

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), controller.make_handler())
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _get(self, path, headers=None):  # returns (status, raw_bytes, headers)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers=headers or {})
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.read(), dict(r.headers)

    def test_update_requires_csrf_header(self):
        # stub the worker so the test never triggers a real network update
        orig, controller._start_update = controller._start_update, lambda only=None: True
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._get("/api/update")                      # no CSRF header
            self.assertEqual(cm.exception.code, 403)
            st, body, _ = self._get("/api/update", {"X-Requested-With": "grimoire"})
            self.assertEqual(st, 200)
            self.assertTrue(json.loads(body.decode())["started"])
        finally:
            controller._start_update = orig

    def test_asset_extension_allowlist(self):
        s, _, _ = self._get("/asset?src=loc&path=pics/a.png")     # image OK
        self.assertEqual(s, 200)
        for bad in ("secret.env", "sqli.md"):                     # secrets/source: no
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._get(f"/asset?src=loc&path={bad}")
            self.assertEqual(cm.exception.code, 404)

    def test_resolve_doc_rejects_nul_byte(self):
        self.assertIsNone(model._resolve_doc("loc", "a\x00b.md"))      # no 500/crash

    def test_symlink_escape_blocked(self):
        if (self.src / "passwd-link").is_symlink():
            self.assertIsNone(model._resolve_doc("loc", "passwd-link"))

    def test_index_and_search_work(self):
        _, body, _ = self._get("/api/search?q=magicword")
        hits = json.loads(body.decode())
        self.assertTrue(any(h["source"] == "loc" for h in hits))

    def test_notebook_indexed_as_markdown(self):
        # the .ipynb must be searchable by its markdown text, and the stored body
        # must be converted markdown (no raw notebook JSON keys)
        _, body, _ = self._get("/api/search?q=nbmagicword")
        hits = json.loads(body.decode())
        hit = next((h for h in hits if h["path"] == "nb.ipynb"), None)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["title"], "Glitch NB")        # title from markdown cell
        snip = hit.get("snippet", "")
        self.assertNotIn("cell_type", snip)                # not raw JSON
        # the doc viewer renders it as HTML (fenced code -> <pre>/<code>), not JSON
        _, doc, _ = self._get("/doc?src=loc&path=nb.ipynb")
        self.assertNotIn("&quot;cell_type&quot;", doc.decode())

    def test_readme_extension_indexed_and_fetchable(self):
        # Extra index extensions must also be safe to retrieve through /doc/MCP.
        _, body, _ = self._get("/api/search?q=readmetoken")
        hits = json.loads(body.decode())
        self.assertTrue(any(h["path"] == "lab6B.readme" for h in hits))
        self.assertIn("ret2libc readmetoken", model.doc_text("loc", "lab6B.readme"))
        _, doc, _ = self._get("/doc?src=loc&path=lab6B.readme")
        self.assertIn("ret2libc readmetoken", doc.decode())

    def test_yaml_doc_renders_decoded_unicode(self):
        # a framework-style YAML with escaped unicode must display as real chars
        (self.src / "fw.yml").write_text('name: "S\\xE9curit\\xE9"\n')
        _, body, _ = self._get("/doc?src=loc&path=fw.yml")
        text = body.decode()
        self.assertIn("S\u00e9curit\u00e9", text)     # decoded e-acute
        self.assertNotIn("\\xE9", text)               # raw escape gone

    def test_doc_blocks_non_document_extensions(self):
        # secret.env exists in the source but is not a document type -> refused
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/doc?src=loc&path=secret.env")
        self.assertEqual(cm.exception.code, 404)
        self.assertIsNone(model.doc_text("loc", "secret.env"))
        self.assertIsNotNone(model.doc_text("loc", "sqli.md"))   # real doc still works

    def test_search_snippet_is_escaped(self):
        # the poisoned doc's markup must be escaped (inert text) in the results pane
        _, body, _ = self._get("/api/search?q=magicword")
        blob = body.decode()
        self.assertNotIn("<script>", blob)            # no raw script tag
        self.assertNotIn("<img", blob)                # no raw img tag
        self.assertIn("&lt;script&gt;", blob)         # proves it was escaped
        self.assertIn("magicword", blob)

    def test_no_template_injection(self):
        # f-string HTML building must not evaluate template-like payloads in docs
        for path in ("/doc?src=loc&path=ssti.md", "/api/search?q=zztoken"):
            _, body, _ = self._get(path)
            text = body.decode()
            self.assertNotIn("9359", text)            # 1337*7 must NOT be evaluated
            self.assertIn("1337*7", text)             # literal payload preserved

    def test_sqli_query_is_safe(self):
        status, body, _ = self._get('/api/search?q=' +
                                    urllib.parse.quote('x" OR "1"="1'))
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(body.decode()), list)

    def test_doc_has_strict_csp(self):
        _, _, hdr = self._get("/doc?src=loc&path=evil.md")
        csp = hdr.get("Content-Security-Policy", "")
        self.assertIn("script-src 'nonce-", csp)
        self.assertNotIn("unsafe-inline", csp.split("script-src")[1].split(";")[0])
        self.assertEqual(hdr.get("X-Content-Type-Options"), "nosniff")

    def test_index_page_has_csp(self):
        _, page, hdr = self._get("/")
        self.assertIn("script-src 'nonce-", hdr.get("Content-Security-Policy", ""))
        self.assertIn('<script nonce="', page.decode())

    def test_asset_served_and_locked_down(self):
        status, _, hdr = self._get("/asset?src=loc&path=pics/a.png")
        self.assertEqual(status, 200)
        self.assertEqual(hdr.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("sandbox", hdr.get("Content-Security-Policy", ""))

    def test_path_traversal_blocked(self):
        self.assertIsNone(model._resolve_doc("loc", "../../../../etc/passwd"))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/asset?src=loc&path=../../../../etc/passwd")
        self.assertEqual(cm.exception.code, 404)

    def test_incremental_reindex_detects_change(self):
        src = self.tmp / "docsrc"
        r1 = model._source_rev(src)
        (src / "new.md").write_text("# New\nfresh content")
        self.assertNotEqual(r1, model._source_rev(src))   # content change -> new rev

    # ----- SQL injection: the category filter is a bound parameter, not text ---- #
    def test_search_category_injection_is_parameterized(self):
        # an injection payload in the category filter matches literally (no such
        # category) -> no rows; it must NEVER bypass the filter and dump all rows
        self.assertEqual(model.search("magicword", "web-api' OR '1'='1"), [])
        # the legitimate category still works
        rows = model.search("magicword", "web-api")
        self.assertTrue(any(r[0] == "loc" for r in rows))

    def test_search_match_injection_is_neutralized(self):
        # FTS5/SQL metacharacters are reduced to plain ANDed tokens, never executed:
        # this must not raise and must not dump unrelated rows
        rows = model.search('magicword" OR docs MATCH "*')
        self.assertIsInstance(rows, list)
        # the bare term still works, proving search is functional
        self.assertTrue(any(r[0] == "loc" for r in model.search("magicword")))

    # ----- MCP server (stdio JSON-RPC) ----------------------------------------- #
    def _rpc(self, method, params=None, rid=1):
        return mcp.handle({"jsonrpc": "2.0", "id": rid, "method": method,
                           "params": params or {}})

    def test_mcp_initialize_and_lists(self):
        r = self._rpc("initialize", {"protocolVersion": "2024-11-05"})
        self.assertEqual(r["result"]["serverInfo"]["name"], "grimoire")
        names = [t["name"] for t in self._rpc("tools/list")["result"]["tools"]]
        for t in ("grimoire_search", "grimoire_checklist_material", "grimoire_topic_material"):
            self.assertIn(t, names)
        prompts = [p["name"] for p in self._rpc("prompts/list")["result"]["prompts"]]
        for pr in ("build_checklist", "build_tutorial", "review_techniques"):
            self.assertIn(pr, prompts)

    def test_mcp_tools_call_search_and_fetch(self):
        r = self._rpc("tools/call",
                      {"name": "grimoire_search", "arguments": {"query": "magicword"}})
        hits = json.loads(r["result"]["content"][0]["text"])
        self.assertTrue(any(h["source"] == "loc" for h in hits))
        # snippet handed to the model is plain text (no control-char sentinels)
        self.assertNotIn("\x02", r["result"]["content"][0]["text"])
        r2 = self._rpc("tools/call",
                       {"name": "grimoire_fetch_doc",
                        "arguments": {"source": "loc", "path": "sqli.md"}})
        self.assertIn("magicword", r2["result"]["content"][0]["text"])

    def test_mcp_fetch_doc_traversal_guarded(self):
        r = self._rpc("tools/call",
                      {"name": "grimoire_fetch_doc",
                       "arguments": {"source": "loc", "path": "../../../etc/passwd"}})
        self.assertIn("not found", r["result"]["content"][0]["text"])

    def test_mcp_checklist_prompt(self):
        r = self._rpc("prompts/get",
                      {"name": "build_checklist", "arguments": {"topic": "web api pentest"}})
        text = r["result"]["messages"][0]["content"]["text"]
        self.assertIn("web api pentest", text)
        self.assertIn("- [ ]", text)                  # asks for checklist items

    def test_mcp_topic_material_and_prompts(self):
        # the broad sweep returns deduped cited material
        r = self._rpc("tools/call",
                      {"name": "grimoire_topic_material", "arguments": {"topic": "magicword"}})
        payload = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(payload["topic"], "magicword")
        self.assertTrue(any(m["source"] == "loc" for m in payload["material"]))
        # tutorial + review prompts render with the topic and their own guidance
        tut = self._rpc("prompts/get",
                        {"name": "build_tutorial", "arguments": {"topic": "ble sniffing"}})
        self.assertIn("ble sniffing", tut["result"]["messages"][0]["content"]["text"])
        rev = self._rpc("prompts/get",
                        {"name": "review_techniques",
                         "arguments": {"topic": "kerberoast", "category": "ad-internal"}})
        rtext = rev["result"]["messages"][0]["content"]["text"]
        self.assertIn("kerberoast", rtext)
        self.assertIn("category=ad-internal", rtext)   # category threaded into prompt
        self.assertIn("currency", rtext)               # asks about how current the docs are

    def test_mcp_context_tool_and_prompt_nudge(self):
        ctxfile = self.tmp / "context.yaml"
        ctxfile.write_text(
            "targets: [10.0.0.0/24, app.example.test]\n"
            "hardware: [HackRF One]\n")
        orig, mcp.CONTEXT_PATH = mcp.CONTEXT_PATH, str(ctxfile)
        try:
            r = self._rpc("tools/call",
                          {"name": "grimoire_context", "arguments": {}})
            ctx = json.loads(r["result"]["content"][0]["text"])
            self.assertIn("10.0.0.0/24", ctx["declared"]["targets"])
            self.assertIn("HackRF One", ctx["declared"]["hardware"])
            self.assertIn("interfaces", ctx["detected"])      # host-detected facts
            # every prompt tells the model to load + adapt to the context
            p = self._rpc("prompts/get",
                          {"name": "build_checklist", "arguments": {"topic": "x"}})
            self.assertIn("grimoire_context", p["result"]["messages"][0]["content"]["text"])
        finally:
            mcp.CONTEXT_PATH = orig

    def test_mcp_mode_gates_exec_tools(self):
        orig = mcp.MODE
        try:
            # read mode: env/which/plan are present, install/run are NOT
            mcp.MODE = "read"
            names = [t["name"] for t in self._rpc("tools/list")["result"]["tools"]]
            self.assertIn("grimoire_env", names)
            self.assertIn("grimoire_plan_install", names)
            self.assertNotIn("grimoire_run", names)
            self.assertNotIn("grimoire_install", names)
            # calling an exec tool in read mode is refused (not executed)
            r = self._rpc("tools/call",
                          {"name": "grimoire_run", "arguments": {"command": "echo nope"}})
            self.assertIn("execution disabled", r["result"]["content"][0]["text"])
            # assist mode: exec tools appear and run
            mcp.MODE = "assist"
            names = [t["name"] for t in self._rpc("tools/list")["result"]["tools"]]
            self.assertIn("grimoire_run", names)
            r = self._rpc("tools/call",
                          {"name": "grimoire_run", "arguments": {"command": "echo gx_ok"}})
            self.assertIn("gx_ok", r["result"]["content"][0]["text"])
        finally:
            mcp.MODE = orig

    def test_mcp_install_rejects_injection(self):
        orig = mcp.MODE
        try:
            mcp.MODE = "auto"
            r = self._rpc("tools/call",
                          {"name": "grimoire_install", "arguments": {"tool": "x; id #"}})
            self.assertIn("refused", r["result"]["content"][0]["text"])
        finally:
            mcp.MODE = orig

    def test_mcp_run_honors_scope(self):
        orig_mode, orig_scope = mcp.MODE, mcp.SCOPE
        try:
            mcp.MODE, mcp.SCOPE = "auto", ["10.0.0.0/24"]
            r = self._rpc("tools/call",
                          {"name": "grimoire_run", "arguments": {"command": "echo 8.8.8.8"}})
            self.assertIn("out-of-scope", r["result"]["content"][0]["text"])
        finally:
            mcp.MODE, mcp.SCOPE = orig_mode, orig_scope

    def test_mcp_notifications_and_errors(self):
        # a notification (no id) gets no response
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0",
                                      "method": "notifications/initialized"}))
        # unknown tool -> JSON-RPC error
        self.assertIn("error", self._rpc("tools/call", {"name": "nope", "arguments": {}}))
        self.assertIn("error", self._rpc("does/not/exist"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
