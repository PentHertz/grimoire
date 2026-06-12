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
./grimoire.py index                      # incremental: only re-indexes changed sources
./grimoire.py index --force              # full rebuild from scratch
./grimoire.py build                      # OPTIONAL native mdbook/mkdocs render
```

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
