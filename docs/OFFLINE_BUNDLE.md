# Grimoire offline bundle

Grimoire does not need a vector database or embedding model to run offline. Its
baseline offline artifact is the fetched source corpus plus the SQLite FTS
index. If you install the optional `hybrid` extra, vector rows are stored in the
same `data/index.db` file via `sqlite-vec`; there is still no separate vector
database service to ship.

## Build with network access

```bash
uv run python -m grimoire_app all
```

To mirror linked writeups/PDFs/articles before indexing:

```bash
uv run python -m grimoire_app links fetch --depth 2
uv run python -m grimoire_app index
```

To include the optional BM25+vector index:

```bash
uv run --extra hybrid python -m grimoire_app index --force
uv run --extra hybrid python -m grimoire_app hybrid status
```

For real semantic vectors, set `GRIMOIRE_EMBED_COMMAND` and
`GRIMOIRE_VECTOR_DIM` during indexing and at runtime. The command must be local,
read text from stdin, and print a JSON float list. Without it, Grimoire uses a
deterministic hash embedder as an offline baseline.

To build only the low-level exploitation corpus:

```bash
uv run python -m grimoire_app fetch --only pwn-notes heap-exploitation rpisec-mbe trailofbits-ctf pwntools-tutorial pwndbg-docs gef-docs linux-kernel-exploitation windows-kernel-resources p0-0days-in-the-wild p0tools awesome-vulnerability-research google-fuzzing aflplusplus-docs syzkaller-docs
uv run python -m grimoire_app links fetch --depth 2 --only pwn-notes heap-exploitation rpisec-mbe trailofbits-ctf pwntools-tutorial pwndbg-docs gef-docs linux-kernel-exploitation windows-kernel-resources p0-0days-in-the-wild p0tools awesome-vulnerability-research google-fuzzing aflplusplus-docs syzkaller-docs
uv run python -m grimoire_app index
```

## Ship these artifacts

- `grimoire.py`
- `grimoire_app/`
- `sources.yaml`
- `data/sources/`
- `data/linked/`, if you mirrored outbound linked documents
- `data/index.db`
- `data/index_state.json`
- `custom/`, if you use local notes or PDFs

At runtime, start the UI or MCP server without network access:

```bash
uv run python -m grimoire_app serve --host 127.0.0.1 --port 8000
uv run python -m grimoire_app mcp
```

If the bundle was built with the `hybrid` extra, use the same extra at runtime
so `sqlite-vec` can load:

```bash
uv run --extra hybrid python -m grimoire_app serve
uv run --extra hybrid python -m grimoire_app mcp
```

## Refresh flow

Rebuild the bundle in a connected environment with `uv run python -m grimoire_app
update`; if you mirror links, also run `uv run python -m grimoire_app links fetch
--depth 2` and `uv run python -m grimoire_app index`. Replace `data/sources/`,
`data/linked/`, `data/index.db`, and `data/index_state.json` in the offline
image. Keep `sources.yaml` with the image so source names and doc provenance
match the index.

## License note

Grimoire links back to every upstream document, but third-party sources keep
their own licenses and attribution requirements. Review source licenses before
redistributing a pre-fetched bundle.
