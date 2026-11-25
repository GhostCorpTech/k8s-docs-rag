# Architecture decision records

Short records of decisions that shaped the system: the context, the options considered, what was chosen and the evidence behind it. Format: [Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| # | Decision | Status |
|---|---|---|
| [0001](0001-hybrid-retrieval-with-rrf.md) | Hybrid retrieval: dense + BM25 fused with RRF | Accepted |
| [0002](0002-token-exact-chunking.md) | Chunk with the embedding model's own tokenizer | Accepted |
| [0003](0003-reranker-off-by-default.md) | LLM reranker off by default | Accepted |
| [0004](0004-sqlite-fts5-for-bm25.md) | SQLite FTS5 for BM25 instead of a search cluster | Accepted |
| [0005](0005-strict-eval-graceful-chat.md) | Strict evaluation, graceful degradation in chat | Accepted |
| [0006](0006-qdrant-named-volume.md) | Qdrant storage on a named volume, verified restores | Accepted |
