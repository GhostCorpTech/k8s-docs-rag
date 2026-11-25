# 0005 — Strict evaluation, graceful degradation in chat

**Status:** accepted · **Date:** 2026-11-25

## Context
Provider calls fail: timeouts, malformed JSON from the reranker, occasional HTTP errors. v1 converted reranker/judge parse failures into a score of 0, which silently distorted metrics (5 zero judge scores and 6 all-zero rerank lists that could not be attributed afterwards).

## Decision
The same retrieval code runs in two modes:

| Failure | Evaluation (`strict=True`) | Chat (`strict=False`) |
|---|---|---|
| Invalid LLM output (JSON, IDs, scores, truncation) | retried, then the run stops | retried, then degrades |
| Reranker fails | stop | keep hybrid order, report `rerank_failed` |
| Translation fails or exceeds 8 s | stop (no time cap) | one attempt, then BM25 on the original question, report `translation_failed` |
| 401 / 402 / 403 | stop | stop — never hidden |

Degradations are returned in the retrieval trace and shown in the UI; every error carries the stage, the model and a diagnostic ID that matches a log record.

## Evidence
On 25 Nov the provider started rejecting most fresh requests with HTTP 400 `invalid request` — including trivial ones like "Reply with the single word: ok" — while identical, previously successful prompts kept succeeding. In a run of 10 chat questions during that incident, 3 of 5 RU→EN translations failed; all 10 questions were still answered from hybrid retrieval with the original wording. Successful translations take 1.6 s median / 5.4 s p90, failing ones hung 10–24 s, hence the 8-second cap for chat.

## Consequences
- A metric can never change because a provider hiccupped; an interrupted run resumes from checkpoints.
- Users get an answer from hybrid search instead of an error, with an honest notice.
