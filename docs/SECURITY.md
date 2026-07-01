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
- **No stored XSS (two independent layers).** Search snippets and PDF/YAML text
  are HTML-escaped; rendered markdown is additionally sanitized with `nh3`
  (`<script>`/`on*`/`javascript:`/`<style>`/`<meta>` stripped) **and** served
  under a strict per-load CSP nonce (`script-src 'nonce-...'`, no
  `unsafe-inline`) - so a poisoned doc stays inert even if either layer is
  weakened. Pages also send `X-Frame-Options: SAMEORIGIN` and `frame-ancestors`
  so the update UI cannot be clickjacked. (`test_*_csp`,
  `test_markdown_html_sanitized`, `test_clickjacking_protection`,
  `test_search_snippet_is_escaped`.)
- **No template injection.** HTML is built with f-strings/escaping, never a
  template engine on user data. (`test_no_template_injection`.)
- **Path traversal blocked.** `_resolve_doc` resolves and confines paths to the
  source dir (symlink escapes and NUL bytes handled). `/asset` is limited to
  image/PDF extensions; `/doc` and `grimoire_fetch_doc` to document extensions
  (`config.DOC_EXT`) - so the server cannot be used to read `.git/config`,
  `.env`, keys, or source. Source **names** are also validated as slugs, so a
  crafted/imported manifest `name` (`../x`, `/abs`) cannot make fetch, index, or
  the `.git` prune escape the sources dir. (`test_path_traversal_blocked`,
  `test_asset_extension_allowlist`, `test_doc_blocks_non_document_extensions`,
  `test_unsafe_source_name_is_rejected`.)
- **CSRF guard** on the state-changing `/api/update` (custom header, no CORS),
  backed by anti-clickjacking framing headers (see XSS above).
- **MCP server is crash-resistant.** A malformed JSON-RPC message (non-object,
  or non-dict `params`) returns a JSON-RPC error instead of killing the stdio
  loop - no remote one-line DoS. (`test_mcp_malformed_request_does_not_crash`.)
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
- **Target scope is best-effort, but fail-closed.** When a scope is set, a
  command is refused if it targets an out-of-scope host, hides the target behind
  shell substitution/indirection (`$(...)`, backticks, `${...}`), or pipes a
  network fetch into a shell/interpreter. Decimal/hex/octal IP encodings are
  decoded before the check, and single-label / `user@host` targets are flagged.
  GitHub is no longer auto-exempt (opt back in per host with
  `GRIMOIRE_SCOPE_ALLOW`). It still resolves nothing at runtime, so a name that
  *resolves* out of scope, or a target read from a file, can slip past - enforce
  real scope at the network layer too. (`test_runner_scope_bypasses_closed`.)
- **Indirect prompt injection.** A poisoned indexed doc can contain instructions
  the AI may follow. In `auto` mode (no per-call approval) this can lead to
  unintended installs/commands. **Recommended posture: `read` for knowledge
  work; `assist` (your MCP client approves every call) for live engagements;
  `auto` only in a contained lab.**
- **`grimoire build` runs upstream build systems.** The optional `build` command
  invokes each source's own `mdbook`/`mkdocs` build, which can execute code/
  plugins defined in a poisoned repo. It is optional and not used by search
  (which reads raw markdown). Only run `build` on sources you trust.
- **`serve` has no authentication.** It binds `127.0.0.1` by default. If you
  expose it (`--host 0.0.0.0`) put it behind a VPN/reverse proxy with auth -
  anyone who can reach it can search and read indexed docs and trigger updates.
- **The engagement context is sent to the model.** Do not put secrets in
  `context.yaml` that you would not want the attached model to see.

## Reporting

This is part of the RF-Swift toolkit by Penthertz (https://penthertz.com).
Report security issues privately rather than in a public issue.
