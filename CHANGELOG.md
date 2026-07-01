# Changelog

All notable changes to Grimoire are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] - 2026-07-01

### Added
- **Proper reStructuredText rendering.** `.rst` docs (Sphinx sources such as
  PySDR) now render via `docutils` in the viewer instead of being mangled by the
  markdown renderer - directives (`.. image::`), `` `text <url>`_ `` links and
  section headings display correctly, with images rewritten to `/asset`.
  Untrusted-safe: `.. raw::` / `.. include::` are disabled and the output is
  nh3-sanitized. New dependency: `docutils>=0.21`.

### Changed
- Version `1.1.0` -> `1.1.1` (package + MCP `serverInfo`).

## [1.1.0] - 2026-07-01

### Added
- **Source: Awesome Cybersecurity Handbooks** (`0xsyr0`) under the `wikis`
  category - a broad, multi-domain markdown handbook collection.
- **Disk-saving fetch.** A source with a `docs_dir` is now sparse-checked-out to
  just that subtree, so large tool repos no longer land whole on disk.
- **`--prune-git` / `GRIMOIRE_PRUNE_GIT`.** Opt-in deletion of each source's
  `.git` after fetch (~40% smaller checkouts); ideal for baking an offline /
  RF-Swift image. `.git` is kept by default for fast incremental `git pull`
  updates.
- **HTML sanitizer (`nh3`).** Rendered markdown is now scrubbed as a second XSS
  layer beneath the doc CSP. Degrades gracefully to CSP-only if `nh3` is absent.
- **CI workflow** (GitHub Actions): unit + security tests across Python
  3.9-3.12, plus a packaging/CLI smoke check.
- This **CHANGELOG**.

### Changed
- Version `1.0.0` -> `1.1.0` (package + MCP `serverInfo`).
- `SECURITY.md` and `README.md` updated for the new controls and test coverage.

### Fixed
- **Environment detection crash as non-root.** `rfswift_scripts_dir()` probed
  hardcoded candidate paths (incl. `/root/scripts`); as a non-root user that
  `is_dir()` raised `PermissionError` and took down `detect_env()` /
  `grimoire_plan_install`. Candidate probing now tolerates inaccessible paths.

### Security
- **Source-name path traversal fixed.** A crafted/imported manifest `name`
  (`../x`, `/abs`, `.git`) is now rejected at load, so fetch / index / the
  `.git` prune can no longer `rmtree`/clone/write outside the sources dir.
- **`git sparse-checkout` option injection fixed.** A `sparse:`/`docs_dir` value
  beginning with `-` can no longer be parsed as a git flag (`--` separator).
- **Exec-mode scope hardened (fail-closed).** With a scope set, commands are now
  refused when they hide the target behind shell substitution (`$(...)`,
  backticks, `${...}`) or pipe a network fetch into a shell/interpreter;
  decimal/hex/octal-encoded IPs are decoded before the check, and single-label /
  `user@host` targets are flagged. `github.com` / `raw.githubusercontent.com`
  are no longer auto-exempt from scope (re-add per host via
  `GRIMOIRE_SCOPE_ALLOW`).
- **MCP stdio DoS fixed.** A malformed JSON-RPC message (a non-object line or a
  non-dict `params`) now returns a JSON-RPC error instead of crashing the
  server loop.
- **Clickjacking blocked.** All responses send `X-Frame-Options: SAMEORIGIN` and
  pages carry `frame-ancestors` (`'none'` on the search UI, `'self'` for the
  same-origin doc viewer), so an overlay page can't drive the CSRF-protected
  `/api/update`.
- New regression tests for every item above (`python3 -m unittest`).

## [1.0.0]

Initial release: offline aggregation and unified FTS5 search over 20+ curated
security knowledge bases; dependency-free web UI (`serve`); optional native
mdBook/mkdocs builds; an MCP server (`mcp`) with read / assist / auto modes and
a gated command runner; and a security test suite (SQLi, XSS, SSTI, CSRF,
path-traversal).

[1.1.1]: https://github.com/PentHertz/grimoire/releases/tag/v1.1.1
[1.1.0]: https://github.com/PentHertz/grimoire/releases/tag/v1.1.0
[1.0.0]: https://github.com/PentHertz/grimoire/releases/tag/v1.0.0
