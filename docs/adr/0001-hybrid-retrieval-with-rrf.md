# 0001 — Hybrid retrieval: dense + BM25 fused with RRF

**Status:** accepted · **Date:** 2026-11-14, evidence added 2026-11-25

## Context
Questions arrive in Russian, the documentation is in English, and many questions hinge on exact identifiers (`PodDisruptionBudget`, `--dry-run=server`, `terminationGracePeriodSeconds`). Dense retrieval handles paraphrase and the language gap; lexical retrieval handles identifiers. The two produce scores on unrelated scales.

## Options
1. Dense only (multilingual E5).
2. BM25 only, on an LLM-translated English query.
3. Weighted sum of normalised scores — needs per-corpus calibration of two score scales.
4. **Reciprocal Rank Fusion** of the two ranked lists, `score = Σ w / (k + rank)`.

## Evidence
Ablations on the 50 benchmark questions, window chunks, top-10, same index as the benchmark ([raw](../../eval/experiments/retrieval-ablations)). The default hybrid reproduces the benchmark exactly (50/50 identical top-10 lists).

| Variant | Recall@5 | MRR@10 | Δ MRR vs hybrid | p |
|---|---:|---:|---:|---:|
| **Hybrid, RRF k = 60** (default) | **0.96** | **0.793** | — | — |
| Dense only | 0.90 | 0.665 | −0.127 [−0.23, −0.02] | 0.019 |
| BM25 only (translated query) | 0.87 | 0.665 | −0.128 [−0.21, −0.04] | 0.004 |
| BM25 only, untranslated Russian query | 0.67 | 0.443 | −0.349 [−0.47, −0.23] | < 0.001 |
| Hybrid without query translation | 0.86 | 0.651 | −0.142 [−0.23, −0.05] | 0.004 |
| RRF k = 10 / 30 / 100 | 0.92 / 0.96 / 0.96 | 0.800 / 0.789 / 0.793 | ≤ ±0.007 | ≥ 0.78 |
| Dense weight × 2 / BM25 weight × 2 | 0.94 / 0.96 | 0.795 / 0.794 | ≤ +0.003 | ≥ 0.92 |
| No per-page diversity cap | 0.95 | 0.787 | −0.006 | 0.24 |

On **heading** chunks the picture differs: dense-only 0.659 vs hybrid 0.652 MRR@10 (p = 0.91), BM25-only 0.632 — no gain from fusion there, while dropping query translation still costs 0.20 MRR (p = 0.003). Full table: [EVALUATION.md §13](../EVALUATION.md#13-retrieval-ablations).

## Decision
Hybrid retrieval with equal-weight RRF, k = 60, dense top-30 + BM25 top-30, at most two chunks per page, BM25 run on an LLM-translated English query (cached per question).

## Consequences
- On the default window chunks each channel alone loses ~0.13 MRR; together they are clearly better (p ≤ 0.02 for both). On heading chunks fusion neither helps nor hurts — the benefit belongs to the combination of window chunking and hybrid search, which is the shipped default.
- The gain depends on query translation: BM25 over the raw Russian question is the weakest variant, and hybrid without translation is no better than either channel alone. Translation is therefore worth an LLM call — and, because the provider sometimes fails it, the chat falls back to the original wording after an 8-second cap instead of failing ([0005](0005-strict-eval-graceful-chat.md)).
- RRF is insensitive to k and channel weights here, so no tuning is needed and nothing is overfitted to 50 questions.
- The diversity cap has no measurable effect on these metrics; it is kept because it keeps one long page from filling the answer context, which the metrics do not capture.
