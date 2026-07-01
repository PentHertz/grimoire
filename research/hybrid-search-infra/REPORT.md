# Hybrid Search Infrastructure Recommendation

## Short Answer

For Grimoire, start with **SQLite FTS5 + sqlite-vec** as the native implementation:

- keep BM25 in SQLite FTS5
- add `sqlite-vec` for local vector candidates
- combine both result sets with reciprocal rank fusion or weighted rank fusion in Grimoire
- keep qmd as a reference implementation and optional sidecar, not the core dependency

This matches Grimoire's offline bundle shape best: no daemon, no cluster, one local database, Python-friendly, source-aware metadata joins, and easy packaging compared with the service engines.

## Ranking

1. **SQLite FTS5 + sqlite-vec**: best default for Grimoire. It is embedded, offline, Python-friendly, and close to the current index model. The tradeoff is that Grimoire must own chunking, embedding, fusion, and reranking.
2. **LanceDB**: best embedded upgrade if we want native vector plus full-text plus hybrid search without writing as much fusion logic. More complex than SQLite, but no service required.
3. **qmd**: best agent-ready sidecar/reference. It already uses the shape we want: SQLite FTS5, sqlite-vec, local embeddings, RRF, query expansion, reranking, and MCP. It is less ideal as Grimoire's core because it is Bun/TypeScript, has weaker metadata filtering, and local testing hit SQLite locking plus GPU OOM on the combined `query` path.
4. **Tantivy + separate vector store**: strongest lexical/BM25 library path, but not a complete hybrid stack by itself. Good if we later want faster richer lexical search and are willing to add Rust/native packaging.
5. **Weaviate**: best turnkey BM25F plus vector service. Too heavy for the default offline Grimoire bundle, but credible as an optional server backend.
6. **Qdrant**: best vector-first service and hybrid sparse+dense engine. Less ideal if we specifically want conventional BM25, because its lexical path is sparse-vector oriented rather than a classic Lucene-style inverted-index BM25 engine.
7. **Vespa**: best serious search/ranking platform. Also the most obviously overpowered for a local CLI bundle.
8. **OpenSearch**: proven BM25 plus kNN/hybrid search server. Heavy operationally and not worth it unless we already want an OpenSearch service.
9. **Typesense**: simpler search server with vector/hybrid support, but less attractive as a dependency because it is still a daemon and has GPLv3 licensing implications.
10. **Meilisearch**: lightweight and pleasant, but less compelling for precise BM25/vector/RAG control than the options above.

## Implementation Shape

Use one canonical SQLite index:

- `documents`: source, path, URL, title, content hash, fetched timestamp, MIME/type
- `chunks`: document id, chunk id, text, offsets, token count
- `chunks_fts`: FTS5 table over chunk text, title, path, URL, symbols
- `chunk_vectors`: `sqlite-vec` table keyed by chunk id
- optional `embeddings_meta`: model id, dimension, quantization, normalization, created_at

Query path:

1. Run FTS5 BM25 for lexical candidates.
2. Embed the query locally and run sqlite-vec kNN for semantic candidates.
3. Fuse candidates with RRF.
4. Apply source/path/category filters before or during candidate retrieval.
5. Return provenance-first snippets with source URL/path and mirrored local file.
6. Add reranking only after this baseline is measured.

## Notes From Local qmd Test

The qmd heap-exploitation slice indexed 847 markdown files into 11,709 vector chunks. BM25 and vector search worked well. The combined `qmd query` path failed locally with CUDA OOM when it tried to load the reranking/generation stack, and concurrent qmd commands can lock its SQLite database.

That makes qmd very useful as a working reference, but not something I would put directly under Grimoire's default query path.

## Primary Sources

- qmd: https://github.com/tobi/qmd
- SQLite FTS5: https://www.sqlite.org/fts5.html
- sqlite-vec: https://github.com/asg017/sqlite-vec
- LanceDB hybrid search: https://docs.lancedb.com/search/hybrid-search
- Tantivy: https://github.com/quickwit-oss/tantivy
- Qdrant hybrid queries: https://qdrant.tech/documentation/search/hybrid-queries/
- Weaviate hybrid search: https://docs.weaviate.io/weaviate/search/hybrid
- Vespa nearest neighbor and ranking docs: https://docs.vespa.ai/en/nearest-neighbor-search.html
- OpenSearch hybrid search: https://docs.opensearch.org/docs/latest/vector-search/ai-search/hybrid-search/
- Typesense vector search: https://typesense.org/docs/guide/vector-search.html
- Meilisearch vector search: https://www.meilisearch.com/docs/learn/experimental/vector_search
