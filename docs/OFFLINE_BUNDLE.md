# Grimoire offline bundle

Grimoire does not need a vector database or embedding model to run offline. Its
offline artifact is the fetched source corpus plus the SQLite FTS index.

## Build with network access

```bash
uv run python -m grimoire_app all
```

To build only the low-level exploitation corpus:

```bash
uv run python -m grimoire_app fetch --only pwn-notes heap-exploitation rpisec-mbe trailofbits-ctf pwntools-tutorial pwndbg-docs gef-docs linux-kernel-exploitation windows-kernel-resources p0-0days-in-the-wild p0tools awesome-vulnerability-research google-fuzzing aflplusplus-docs syzkaller-docs
uv run python -m grimoire_app index
```

## Ship these artifacts

- `grimoire.py`
- `grimoire_app/`
- `sources.yaml`
- `data/sources/`
- `data/index.db`
- `data/index_state.json`
- `custom/`, if you use local notes or PDFs

At runtime, start the UI or MCP server without network access:

```bash
uv run python -m grimoire_app serve --host 127.0.0.1 --port 8000
uv run python -m grimoire_app mcp
```

## Refresh flow

Rebuild the bundle in a connected environment with `uv run python -m grimoire_app
update`, then replace `data/sources/`, `data/index.db`, and
`data/index_state.json` in the offline image. Keep `sources.yaml` with the image
so source names and doc provenance match the index.

## License note

Grimoire links back to every upstream document, but third-party sources keep
their own licenses and attribution requirements. Review source licenses before
redistributing a pre-fetched bundle.
