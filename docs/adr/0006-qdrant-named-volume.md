# 0006 — Qdrant storage on a named volume, verified restores

**Status:** accepted · **Date:** 2026-11-25

## Context
The original `docker-compose.yml` mounted a host folder (`./data/qdrant`) into Qdrant. On the Windows development machine the running Qdrant served correct vectors from memory — the benchmark's dense scores are a healthy 0.80–0.91 — but the vector files on disk were not: snapshots exported later contained **all-zero vectors in every segment** (32,088 of 32,088), while IDs and payloads were intact. A restore check that compared the restored collection with the stopped source compared zeros with zeros and passed.

Most likely cause: Qdrant writes vectors through memory-mapped files, and memory-mapped writes are not reliably persisted on Docker Desktop host file shares.

## Decision
- Qdrant storage uses a Docker **named volume** (`qdrant_storage`), never a host bind mount.
- `app.cli verify-index` checks a restored or rebuilt index against the documentation source (page hashes), SQLite (chunk IDs), vector norms, and re-embeds a random sample to compare cosine similarity with the stored vectors.
- `app.cli repair-vectors` re-embeds only vectors that are not unit-norm from the chunk text stored in the payload — no re-chunking, IDs and page hashes unchanged, resumable.

## Consequences
- Data in the named volume is not visible as plain files; backups go through Qdrant snapshots, which are now verified before publishing.
- The lesson generalises: a verification step must compare against an independent source of truth (here: re-embedding the text), not against another copy of the same storage.
