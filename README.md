<img width="2967" height="1484" alt="glitch" src="https://github.com/user-attachments/assets/dc46df80-fd42-45f3-b7e7-079136c0e713" />
# Grimoire

**Offensive knowledge, offline. One search box for every playbook.**

```
   ____      _                 _
  / ___|_ __(_)_ __ ___   ___ (_)_ __ ___
 | |  _| '__| | '_ ` _ \ / _ \| | '__/ _ \
 | |_| | |  | | | | | | | (_) | | | |  __/
  \____|_|  |_|_| |_| |_|\___/|_|_|  \___|
```

> by [Penthertz](https://penthertz.com) - part of the RF-Swift toolkit

Grimoire clones a curated set of security knowledge bases, indexes all of their
markdown/YAML into a single full-text search index, and serves a fast web UI.
Type `ssrf`, `xss`, `sql`, `kerberoast`, `sudo`, `jwt`, ... and it instantly
surfaces the matching pages across *every* source - HackTricks,
PayloadsAllTheThings, the OWASP guides, the living-off-the-land databases, and
your own notes - with a link back to each original.

Built to run anywhere (single Python script, no mandatory services) and to be
embedded in RF-Swift.

```
  GRIMOIRE   offensive knowledge, offline
  > ssrf_   ->  HackTricks . PayloadsAllTheThings . WSTG . API Top 10 ...
```

<img width="2967" height="1484" alt="glitch" src="https://github.com/user-attachments/assets/a30c31bf-3a22-4e5d-9718-e062f2d563a4" />


## Features

- **Unified search** over 20+ sources via SQLite **FTS5** with BM25 ranking -
  one query language regardless of how each repo is authored (mdBook, mkdocs,
  Jekyll, Hugo, plain markdown, YAML).
- **Fully offline** once fetched. No telemetry, no external calls at runtime.
- **Spawnable web service** - `grimoire.py serve` (bind host/port; run it in the
  background or as a container service).
- **Provenance** - every doc shows its source and a link to the **original file
  on GitHub**, so you always know where guidance came from.
- **Rich rendering** - images display and relative `.md` links navigate inside
  the viewer (relative `<img>`/links are rewritten to a guarded `/asset` / `/doc`
  endpoint). Note: for sparse sources (e.g. Ghidra), images stored outside the
  checked-out paths won't be present - widen the source's `sparse:` list if needed.
- **Copy-ready** - one-click copy buttons on every code/command block.
- **OSINT note** - OSINT tools mostly target individuals; scope and document
  collection to what an engagement justifies (GDPR), especially for named people.
- **Obsidian-friendly** - point it at your vault; `[[wikilinks]]`, `#tags` and
  YAML frontmatter are handled (links/tags become one-click searches).
- **Bring your own docs** - drop markdown in `custom/` or register a local path.
- **Optional native builds** - render a source's own mdBook/mkdocs site when the
  toolchain is present; search never depends on it.
- **Attach an AI model (MCP)** - `grimoire.py mcp` exposes the index over the
  Model Context Protocol, so Claude / Codex / Gemini / any MCP client can search,
  read docs, build source-backed technical checklists, assemble a topic's reads
  into a complete cited tutorial, and review whether the docs are current / find
  better techniques. It also adapts to your **engagement context** (targets,
  interfaces, hardware/SDRs, SIM, RF) so suggestions fit your assessment.
  Default `read` mode is read-only (no shell, no writes); opt into `--mode
  assist|auto` to let it detect/install missing tools (RF-Swift recipe, else the
  host package manager) and run steps, behind a destructive-command denylist and
  a target scope. See [docs/MCP_TUTORIAL.md](docs/MCP_TUTORIAL.md).

## Sources

Curated in [`sources.yaml`](sources.yaml), grouped by category:

| Category | Sources |
|---|---|
| `wikis` | HackTricks, HackTricks Cloud, PayloadsAllTheThings, The Hacker Recipes, six2dez Pentest Book |
| `ad-internal` | InternalAllTheThings, ired.team, OCD mindmaps |
| `hardware-iot` | HardwareAllTheThings |
| `mobile` | OWASP MASTG, OWASP MASVS |
| `web-api` | OWASP WSTG, Cheat Sheet Series, ASVS, API Security Top 10 |
| `lotl` | GTFOBins, LOLBAS, GTFOArgs, LOLDrivers, LOOBins, WADComs |
| `re-books` | mytechnotalent/Reverse-Engineering, Nightmare, how2heap (drop a PDF book in `custom/` to add one) |
| `re-tools` | radare2book, rizin book, angr docs, Ghidra (in-tree docs) |
| `re-indexes` | Awesome-Reversing (ReversingID + tylerha97), reverse-engineering (wtsxDev), Awesome Malware Analysis, Awesome Android RE |
| `firmware` | Awesome Firmware Security |
| `osint` | awesome-osint (jivoi), OSINT Framework (JSON tree), OSINT Collection, Awesome-OSINT-List, osint_stuff_tool_collection, sinwindie/OSINT, Trace Labs awesome-osint |
| `dfir` | awesome-forensics, awesome-incident-response, awesome-memory-forensics, ForensicArtifacts, KapeFiles, CERT-SG IRM, PagerDuty IR, IR-plan-template, Velociraptor, Volatility 3, plaso, Dissect |
| `glitching` | findus/fault-injection-library, ChipWhisperer (+ Jupyter Fault101/201), ChipSHOUTER-PicoEMP, Faulty Cat, SimpleLink-FI, PicoGlitcher-LPC1343 |
| `bluetooth` | awesome-bluetooth-security, BlueToolkit, Sniffle, Ubertooth, InternalBlue, SweynTooth, BrakTooth, KNOB/BIAS/BLUFFS, Mirage, BtleJack, bleah, OpenHaystack, Continuity, apple_bleee, BLE CTF |
| `wifi` | awesome-wifi-security, 0xor0ne awesome-list, MacStealer/FragAttacks/KRACK (Vanhoef), Dragonslayer/Dragondrain/Dragonforce, hcxdumptool/hcxtools, aircrack-ng, AngryOxide, airgeddon, eaphammer, hostapd-mana, wifipumpkin3, DragonShift, WiFiChallengeLab |
| `sdr` | PySDR, ThinkDSP, SDR-for-Engineers (lectures+labs), SDRangel, SDR++, inspectrum, URH, liquid-dsp, mhostetter/sdr, scikit-dsp-comm, CommPy, SoapySDR, learnSDR |
| `compliance` | awesome-compliance, NIST OSCAL, OWASP SAMM (NIST PDFs / CIS / SCF crosswalk / CCM / SANS / ANSSI-EBIOS / NIS2-DORA-CRA / CNIL / CISO Assistant = drop-in or run-the-tool) |

## Install & run

Install as a CLI with pipx (recommended) or pip - this puts `grimoire` on your PATH:

```bash
pipx install grimoire-docs            # or: pipx install .   (from a checkout)
pipx install "grimoire-docs[render]"  # + python-markdown for prettier doc rendering

grimoire all                          # clone every source + build the index
grimoire serve                        # http://127.0.0.1:8000
grimoire mcp                          # attach an AI model over MCP
```

When installed, user state (the editable `sources.yaml`, `custom/`, and the
`data/` index) lives in `$GRIMOIRE_HOME` (default `~/.local/share/grimoire`);
the manifest is seeded from a packaged default on first run.

Or run straight from a checkout (no install):

```bash
pip install -r requirements.txt    # PyYAML + markdown (both optional-degrading)
./grimoire.py all                  # clone every source + build the index
./grimoire.py serve                # http://127.0.0.1:8000
```

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the day-to-day commands and search tips.

## Commands

| Command | What it does |
|---|---|
| `fetch [--only N...]` | git clone/pull sources into `data/sources/` |
| `build` | optional native mdBook/mkdocs render into `data/build/` |
| `index [--force]` | incremental FTS5 index at `data/index.db` (only re-indexes sources whose git commit / content changed; `--force` = full rebuild) |
| `serve [--host H --port P]` | start the web search UI |
| `all [--only N...]` | `fetch` + `index` |
| `update [--only N...]` | refresh docs: `fetch` + `index` (alias of `all`) |
| `mcp` | expose Grimoire over MCP (stdio) so an AI model can attach |

Docs can also be refreshed live from the web UI with the **Update docs** button
(runs a background `fetch` + reindex and streams progress).

## Code layout (MVC)

The entrypoint `grimoire.py` is a thin launcher; the implementation lives in the
`grimoire_app/` package, split cleanly:

| Module | Responsibility |
|---|---|
| `config.py` | filesystem paths + indexing constants (single source of truth) |
| `model.py` | data: sources manifest, fetch, index, the `Index` store + search |
| `view.py` | rendering: markdown/obsidian/pdf/notebook -> safe HTML, CSP'd pages |
| `controller.py` | HTTP handler + CLI commands wiring model and view together |
| `mcp.py` | the MCP server (search/docs/checklist/tutorial/review tools + prompts) |
| `context.py` | engagement context (targets, hardware, SIM, RF) for the MCP layer |
| `runner.py` | gated execution: env detect, install resolver, command runner |

The package also ships `web/` (the UI) and `sources.default.yaml` (the seed
manifest), so a pip/pipx install is self-contained.

All SQL is funnelled through `model.Index`, where every statement is
parameterized (values are bound, never string-formatted), and free-text queries
pass through `_fts_query` (alphanumeric prefix tokens only) before reaching a
MATCH expression - so a poisoned query can break out of neither the SQL nor the
FTS5 grammar. The test suite includes dedicated SQLi, XSS, SSTI, CSRF, and
path-traversal cases (`python3 -m unittest`).

## How it works

- **fetch** shallow-clones each `sources.yaml` repo into `data/sources/<name>`.
- **index** walks every `*.md` / `*.markdown` / `*.mdx` / `*.rst` / `*.yml` /
  `*.yaml` file (`.rst` so Sphinx-documented projects contribute their *full*
  docs, not just the README) and stores it in a SQLite FTS5 table
  (`data/index.db`) with BM25 ranking. This is the unified layer: all sources,
  one query, regardless of authoring format. A source can pull extra extensions
  with `index_ext:` (e.g. `.ipynb`, `.json`) and, to dump the maximum, sources
  are indexed whole unless a `docs_dir:`/`sparse:` is set to scope a huge repo.
- **serve** is a dependency-free `http.server` exposing the UI plus a small API:
  - `GET /` search UI
  - `GET /api/search?q=&cat=` ranked JSON results (with highlighted snippets)
  - `GET /api/sources` categories for the filter chips
  - `GET /doc?src=&path=` renders a doc (markdown -> HTML) with an origin banner,
    copy buttons and Obsidian link/tag handling
- **build** (optional) runs `mdbook`/`mkdocs` when available for pixel-perfect
  browsing; the index always reads raw markdown so the tool works without it.

## Add your own docs

Both are picked up by `grimoire.py index`:

1. **Drop-in** - put markdown in [`custom/`](custom/) (indexed as source `custom`).
2. **Registered path / Obsidian vault** - add to `sources.yaml`:
   ```yaml
   - name: my-vault
     title: My Vault
     type: local
     path: /home/me/ObsidianVault
     category: custom
   ```

## Embedding in RF-Swift

Grimoire is the `grimoire.py` launcher + the `grimoire_app/` package + manifest
+ web dir, with no required services (stdlib `sqlite3` / `http.server`;
`PyYAML` / `markdown` optional). To bake an
offline knowledge base into an image: run `fetch` + `index` at build time, ship
`data/index.db` (and `data/sources/` for the doc viewer), then `grimoire.py
serve` as a runtime command.

## Security

See [docs/SECURITY.md](docs/SECURITY.md) for the threat model, the controls (parameterized
SQL, CSP/XSS, path-traversal and doc-extension allowlists, tool-name and git-URL
validation, default-off execution), and the residual risks you must understand
before exposing `serve` or enabling MCP `--mode assist|auto`.

## License & attribution

Grimoire's own code is released under the **MIT License** (see [`LICENSE`](LICENSE)).

The license covers Grimoire itself only. Grimoire aggregates and indexes
third-party documentation; each source keeps its own license and authorship,
and is cloned at runtime rather than redistributed here. The origin banner in
the viewer links back to the upstream repository for every document. Review and
respect each project's license before redistribution.
