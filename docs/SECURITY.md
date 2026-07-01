# Grimoire - security model

Grimoire aggregates third-party documentation, serves a local web UI, and can
expose itself to an AI model over MCP (optionally with an execution layer). This
document states the threat model, the controls, and the residual risks you must
understand before relying on it - especially the execution modes.

## Threat model

- **Indexed sources are untrusted.** Grimoire clones many third-party repos.
  Their content (markdown, YAML, notebooks, PDFs, images) is treated as hostile
  input: it is rendered, searched, and - in exec mode - read by an AI model.
- **Search queries / paths are untrusted** (anyone who can reach the web UI).
- **The attached AI model is semi-trusted.** In exec mode it can install
  software and run commands; a model can be steered by indirect prompt injection
  from a poisoned indexed document.

## Controls in place (and tested)

- **No SQL injection.** All queries are parameterized via `model.Index`; free
  text is reduced to alphanumeric prefix tokens by `_fts_query` before reaching
  an FTS5 MATCH. (`test_sqli_*`, `test_search_*`.)
- **No stored XSS.** Rendered docs and search snippets are HTML-escaped; pages
  ship a strict per-load CSP nonce (`script-src 'nonce-...'`, no
  `unsafe-inline`), so injected `<script>`/`on*`/`javascript:` cannot run.
  (`test_*_csp`, `test_search_snippet_is_escaped`.)
- **No template injection.** HTML is built with f-strings/escaping, never a
  template engine on user data. (`test_no_template_injection`.)
- **Path traversal blocked.** `_resolve_doc` resolves and confines paths to the
  source dir (symlink escapes and NUL bytes handled). `/asset` is limited to
  image/PDF extensions; `/doc` and `grimoire_fetch_doc` to document extensions
  (`config.DOC_EXT`) - so the server cannot be used to read `.git/config`,
  `.env`, keys, or source. (`test_path_traversal_blocked`,
  `test_asset_extension_allowlist`, `test_doc_blocks_non_document_extensions`.)
- **CSRF guard** on the state-changing `/api/update` (custom header, no CORS).
- **No command injection via tool names.** Install tool names are validated
  against `^[A-Za-z0-9][A-Za-z0-9._+-]*$`; metacharacters are refused before any
  shell command is built. (`test_runner_rejects_tool_name_injection`,
  `test_mcp_install_rejects_injection`.)
- **No git transport RCE.** `fetch` only clones `https/http/git/ssh/git@` URLs;
  `ext::`, `file://`, `fd::` and option-injection (`-x`) are rejected.
  (`test_safe_repo_url_blocks_transport_helpers`.)
- **Execution is off by default.** `mcp` runs in `read` mode unless you pass
  `--mode assist|auto`; the install/run tools are absent from `tools/list` in
  read mode, so a model cannot attempt them. (`test_mcp_mode_gates_exec_tools`.)

## Residual risks - read before release/use

- **The exec denylist is a guardrail, not a sandbox.** It refuses obvious
  host-wreckers (`rm -rf /`, `mkfs`, fork bombs, `shutdown`, ...) but is
  trivially bypassable (`$(...)`, variable expansion, odd spacing). It stops
  accidents, not a determined adversary. Exec mode runs real commands on your
  host/container with your privileges (and `sudo` when available).
- **Target scope is best-effort.** It blocks out-of-scope IPv4/IPv6 literals and
  hostnames, but can be bypassed by decimal/hex/octal IP encodings, by names
  that resolve out of scope, or by tools that take targets from a file. Treat
  scope as defense-in-depth, not a boundary. Enforce real scope at the network
  layer too.
- **Indirect prompt injection.** A poisoned indexed doc can contain instructions
  the AI may follow. In `auto` mode (no per-call approval) this can lead to
  unintended installs/commands. **Recommended posture: `read` for knowledge
  work; `assist` (your MCP client approves every call) for live engagements;
  `auto` only in a contained lab.**
- **`grimoire build` runs upstream build systems.** The optional `build` command
  invokes each source's own `mdbook`/`mkdocs` build, which can execute code/
  plugins defined in a poisoned repo. It is optional and not used by search
  (which reads raw markdown). Only run `build` on sources you trust.
- **`grimoire links fetch` downloads untrusted web content.** It is a bounded
  document mirror, not a browser sandbox: it skips YouTube/video URLs and applies
  size/time caps, but remote HTML/PDF/text is still hostile input. Mirrored docs
  are indexed and rendered under the same CSP/path-extension controls as cloned
  sources. Review licenses before redistributing mirrored content.
- **`serve` has no authentication.** It binds `127.0.0.1` by default. If you
  expose it (`--host 0.0.0.0`) put it behind a VPN/reverse proxy with auth -
  anyone who can reach it can search and read indexed docs and trigger updates.
- **The engagement context is sent to the model.** Do not put secrets in
  `context.yaml` that you would not want the attached model to see.

## Reporting

This is part of the RF-Swift toolkit by Penthertz (https://penthertz.com).
Report security issues privately rather than in a public issue.
