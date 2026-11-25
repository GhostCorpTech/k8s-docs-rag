# 0003 — LLM reranker off by default

**Status:** accepted · **Date:** 2026-11-25

## Context
A listwise LLM reranker (top-20 candidates → JSON scores) was built because rerankers usually help. It costs one extra LLM call with ~12k input tokens per question.

## Evidence
Benchmark, 50 questions, paired comparisons (see [EVALUATION.md](../EVALUATION.md)):

| | Δ MRR@10 with reranker | p | Median added latency |
|---|---:|---:|---:|
| Window chunks | −0.047 [−0.13, +0.04] | 0.30 | 17.1 s |
| Heading chunks | +0.096 [−0.01, +0.20] | 0.08 | 17.1 s |

It consumed 39 % of all input tokens of the benchmark. On window chunks it moved the first relevant page up for 9 questions and down for 11.

## Decision
Default to window chunks with hybrid retrieval and **no reranker**. Keep the reranker as a user-visible toggle and in the evaluation matrix.

## Consequences
- Answers start ~17 s sooner, and LLM spend per question drops to about a third (≈ $0.0003 instead of ≈ $0.0009 at benchmark prices).
- The decision is data-dependent: a dedicated cross-encoder (not an LLM) or a different corpus could change it. The evaluation harness makes re-checking a one-command job.
