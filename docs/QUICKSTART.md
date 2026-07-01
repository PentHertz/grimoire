# Grimoire - quick use

```bash
# 0. deps (small; the tool degrades gracefully without them)
pip install -r requirements.txt          # PyYAML + markdown

# 1. pull every source and build the search index
./grimoire.py all                        # = fetch + index

# 2. open the search UI
./grimoire.py serve                      # http://127.0.0.1:8000
./grimoire.py serve --host 0.0.0.0 --port 8080   # expose on the LAN / container

# 3. (optional) attach an AI model over MCP - search, checklists, tutorials
./grimoire.py mcp                                  # read-only, stdio
./grimoire.py mcp --context context.yaml           # tailor to your targets/hardware
```

See [MCP_TUTORIAL.md](MCP_TUTORIAL.md) for attaching Claude / Codex / Gemini and
[`context.example.yaml`](context.example.yaml) for the engagement-context format.

## Day-to-day

```bash
./grimoire.py update                     # refresh everything: git pull all sources + reindex
./grimoire.py fetch                      # update all sources (git pull) only
./grimoire.py fetch --only hacktricks owasp-wstg
./grimoire.py links scan --only pwn-notes
./grimoire.py links fetch --only pwn-notes --depth 2 --max-mb 25
./grimoire.py index                      # incremental: only re-indexes changed sources
./grimoire.py index --force              # full rebuild from scratch
./grimoire.py hybrid status              # optional sqlite-vec/vector status
./grimoire.py build                      # OPTIONAL native mdbook/mkdocs render
```

`links fetch` mirrors documents linked by source files. It skips YouTube/video
URLs, keeps per-URL size/time caps, stores provenance in `data/linked/`, and
indexes mirrored content as `*-links` sources on the next `index`. By default it
fetches direct links; add `--depth 2` or higher to crawl links found inside
mirrored pages too.

## Optional hybrid search

Pure BM25 works out of the box. To add local vector candidates in the same
SQLite index:

```bash
uv run --extra hybrid python -m grimoire_app index --force
uv run --extra hybrid python -m grimoire_app hybrid status
```

Search keeps the same UI/API/MCP shape; Grimoire fuses FTS5 BM25 and vector
candidate ranks internally. The default embedder is a deterministic local hash
baseline for offline plumbing and tests. For real semantic retrieval, point
`GRIMOIRE_EMBED_COMMAND` at a local model command that reads text on stdin and
prints a JSON float list, and set `GRIMOIRE_VECTOR_DIM` to the model dimension.

For an image or air-gapped runtime, build the corpus once with network access,
then ship the fetched source trees plus the SQLite FTS index. See
[OFFLINE_BUNDLE.md](OFFLINE_BUNDLE.md).

You can also refresh from the web UI: click **Update docs** (top-right) - it
git-pulls every source and rebuilds the index in the background, showing live
progress, then refreshes the results in place.

## Search tips (web UI)

- Type a term: `ssrf`, `xss`, `sql`, `kerberoast`, `lsass`, `sudo`, `jwt`, `padding oracle`.
- Multiple words = all-must-match, prefix-matched (`kerb roast` -> `kerb*` `roast*`).
- Click a category chip (wikis / ad-internal / web-api / lotl / ...) to filter.
- `/` focuses the search box, `Esc` clears it.
- Click a result -> the doc opens on the right with:
  - an **origin banner** (source + link to the original file on GitHub),
  - **copy buttons** on every code/command block,
  - Obsidian `[[wikilinks]]` and `#tags` turned into one-click searches.

## Add your own docs

```bash
# drop-in: any markdown here is indexed under the "custom" source
cp ~/notes/*.md custom/ && ./grimoire.py index

# or point at an Obsidian vault (wikilinks/tags/frontmatter handled) via sources.yaml:
#   - name: my-vault
#     title: My Vault
#     type: local
#     path: /home/me/ObsidianVault
#     category: custom
./grimoire.py index
```
