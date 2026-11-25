# Eval run 20261114T152921Z

Recall@k: mean fraction of relevant pages found in the first k chunks; duplicate pages count once.
HitRate@k: fraction of questions with at least one relevant page. MRR ranks chunks; anchors are ignored.

> Legacy Recall was HitRate; Recall is now recomputed as macro-average fraction of relevant pages found (anchors ignored).
> Legacy faithfulness is unverified: judge saw up to 10 hits, generation up to 8. Errors parsing scores were silently replaced by zero.
> Legacy embeddings used character windows and truncated overlong E5 inputs. Do not compare this run with token-aware v4 indexes as a router-only experiment.
> Provider metadata was saved only at completion, not at start; original raw provider responses are unavailable.

| Chunking | Reranker | Recall@5 | Recall@10 | MRR@10 | Faithfulness |
|---|---:|---:|---:|---:|---:|
| heading | off | 0.610 | 0.770 | 0.336 | 0.967 |
| heading | on | 0.910 | 0.940 | 0.707 | 0.973 |
| window | off | 0.730 | 0.910 | 0.353 | 0.962 |
| window | on | 0.900 | 0.960 | 0.727 | 0.871 |

## Suspicious legacy records (not proof of failure)

- window_rerank_on / q024: zero faithfulness: genuine zero or swallowed JSON error; raw provider response was not saved
- window_rerank_on / q024: all rerank scores zero: genuine tie or swallowed JSON error
- window_rerank_on / q026: all rerank scores zero: genuine tie or swallowed JSON error
- window_rerank_on / q027: zero faithfulness: genuine zero or swallowed JSON error; raw provider response was not saved
- window_rerank_on / q028: all rerank scores zero: genuine tie or swallowed JSON error
- window_rerank_on / q034: zero faithfulness: genuine zero or swallowed JSON error; raw provider response was not saved
- window_rerank_on / q034: all rerank scores zero: genuine tie or swallowed JSON error
- window_rerank_on / q036: all rerank scores zero: genuine tie or swallowed JSON error
- window_rerank_on / q042: all rerank scores zero: genuine tie or swallowed JSON error
- window_rerank_on / q048: zero faithfulness: genuine zero or swallowed JSON error; raw provider response was not saved
- window_rerank_on / q049: zero faithfulness: genuine zero or swallowed JSON error; raw provider response was not saved
