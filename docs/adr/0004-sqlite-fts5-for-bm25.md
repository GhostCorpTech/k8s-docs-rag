# 0004 — SQLite FTS5 for BM25 instead of a search cluster

**Status:** accepted · **Date:** 2026-11-14

## Context
Hybrid retrieval needs a lexical index next to Qdrant. Typical choices are Elasticsearch/OpenSearch or Qdrant's own sparse vectors.

## Options
1. Elasticsearch/OpenSearch — mature BM25, but a JVM service with 1–2 GB RAM for ~32k chunks.
2. Qdrant sparse vectors (BM25/SPLADE) — one store, but BM25 statistics must be maintained by the client, and SPLADE needs another model.
3. **SQLite FTS5** — built into Python, transactional, a single file.

## Decision
Option 3, with `unicode61 remove_diacritics 2` tokenisation and `bm25()` field weights title 3 · heading 2 · body 1. All user tokens are quoted so input can never be parsed as FTS syntax. The same SQLite file holds the per-page state used for incremental indexing and the query-translation cache, so index state and lexical index commit together.

## Consequences
- No extra service; the lexical index ships as one file inside the index snapshot.
- FTS5 cannot match Russian queries against English text; an LLM translates the query to English keywords once per distinct question (cached).
- Fine for tens of thousands of chunks; a multi-million-chunk corpus would justify a dedicated engine.
