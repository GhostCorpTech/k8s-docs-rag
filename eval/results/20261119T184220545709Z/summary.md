# Eval run 20261119T184220545709Z

Recall@k: mean fraction of relevant pages found in the first k chunks; duplicate pages count once.
HitRate@k: fraction of questions with at least one relevant page. MRR ranks chunks; anchors are ignored.


| Chunking | Reranker | Recall@5 | Recall@10 | MRR@10 | Faithfulness |
|---|---:|---:|---:|---:|---:|
| heading | off | 0.920 | 0.960 | 0.660 | 0.955 |
| heading | on | 0.870 | 0.980 | 0.756 | 0.966 |
| window | off | 0.960 | 1.000 | 0.793 | 0.975 |
| window | on | 0.930 | 1.000 | 0.746 | 0.976 |
