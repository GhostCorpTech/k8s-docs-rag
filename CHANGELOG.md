# Changelog

## 1.0.0 — 2026-11-25

Portfolio release.

### Live checks (25 Nov, $0.061 metered)
- Citation A/B on recorded contexts with a control arm: the unchanged prompt now cites in 50/50 answers (was 34/50 in the benchmark); a stricter prompt brings no significant gain, so the prompt is unchanged. Recorded in `eval/experiments/citation-prompt/`.
- HTTP 400 diagnostics: 0/43 application-shaped calls failed, incl. 20 identical requests; `stream_options` reproducibly returns 400 and is never sent. Recorded in `eval/experiments/http400-diagnostics/`.
- Budget-capped experiment harness `scripts/experiments/live_checks.py`.
- Model prices are looked up per model with a `*` provider entry; the router endpoint is no longer stored in the repository (redacted to a neutral label in recorded logs).

### Added
- Streaming chat endpoint `POST /api/chat/stream` (Server-Sent Events): sources before tokens, stop/abort, truncation detection; reasoning deltas are never forwarded.
- Follow-up questions: conversation history (last 6 messages) and an LLM rewrite of the follow-up into a standalone retrieval query.
- Retrieval trace in every chat/search response: BM25 query, candidate counts, per-stage timings, degradation flags; dense/BM25 ranks and RRF/rerank scores on every citation.
- Graceful degradation in interactive mode: a failing reranker falls back to RRF order, a failing translation falls back to the original query; auth/billing errors (401/402/403) are never degraded. Evaluation stays strict.
- Actionable error when the requested index is empty (instead of a low-level connection error); empty-index notice in the UI.
- `GET /api/eval/latest/questions` — per-question ranks and faithfulness for every configuration.
- New UI: conversation sidebar, Markdown answers with citation chips and hover cards, source cards, retrieval inspector, retrieval settings, evaluation dashboard (KPIs, metric table, rank distribution, per-question matrix), light/dark themes, mobile layout.
- `scripts/analyze_eval.py`: bootstrap CIs, paired permutation tests, rank distribution, citation compliance, latency and cost per operation, SVG charts (light/dark).
- Screenshot replay harness that renders recorded benchmark answers through the real UI.
- CI: backend lint/format/tests, frontend build, re-derivation of published evaluation tables.
- Apache-2.0 license, NOTICE with CC BY 4.0 attribution for Kubernetes documentation excerpts.

### Changed
- Defaults switched to the benchmark-best configuration: `window` chunks, reranker off.
- Answer prompt answers in the language of the question (was: always Russian).
- Frontend served same-origin behind nginx (`/api` proxied, SSE unbuffered); no build-time API URL, no CORS in production.
- All frontend dependencies pinned to exact versions (were `latest`).
- API error messages in English with stage, model and diagnostic ID.
- Ruff rule set made explicit; 81 tests (was 62), branch+line coverage 73 % (was 68 %).

## 0.4 — 2026-11-19 (audited pipeline, benchmark run `20261119T184220545709Z`)

- Token-exact chunking with the E5 tokenizer (v1 silently truncated 22–33 % of chunks).
- Hugo `code_sample` / `codenew` / `code` / `include` expansion; anchors, slugs and heading translations fixed.
- Recall redefined as share of gold pages (v1 reported HitRate); judge sees exactly the generator's context; invalid LLM output is retried, never scored as 0.
- Manifest-pinned, checkpointed, resumable, parallel evaluation with per-call usage and cost logging.
- Full re-index (1,730 pages; 19,463 heading / 12,625 window chunks) and a 200-answer benchmark: $0.156 metered.

## 0.1 — 2026-11-14

- First hybrid RAG prototype and evaluation run `20261114T152921Z` (kept with an offline audit).
