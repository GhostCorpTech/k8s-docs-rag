"""Retrieval ablations on the benchmark questions. No LLM calls, $0.

For each chunking strategy, compares dense-only, BM25-only and hybrid RRF variants
(k, channel weights, diversity, query translation) using the same index the benchmark used.
BM25 queries are the English translations recorded in the benchmark run, so no API is needed.
The default hybrid variant must reproduce the benchmark's no-rerank ranks - this doubles as an
end-to-end check of the restored index.

Usage (env as for the backend: SQLITE_PATH, QDRANT_URL, COLLECTION_PREFIX, MODEL_CACHE_PATH):
  python scripts/experiments/ablations.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.cloud import CloudModels
from app.config import get_settings
from app.database import Database
from app.evaluation import _rank_of_relevant, _recall
from app.retrieval import Retriever
from app.vector_store import VectorStore

RUN = ROOT / "eval/results/20261119T184220545709Z"
OUT = ROOT / "eval/experiments/retrieval-ablations"
SEED, RESAMPLES, TOP = 20261125, 10_000, 10

# name -> (channels, rrf_k, dense_weight, lexical_weight, diversify, translated_bm25)
VARIANTS = {
    "dense_only": ("dense", 60, 1.0, 0.0, True, True),
    "bm25_only": ("lexical", 60, 0.0, 1.0, True, True),
    "bm25_only_untranslated": ("lexical", 60, 0.0, 1.0, True, False),
    "hybrid_untranslated_bm25": ("both", 60, 1.0, 1.0, True, False),
    "hybrid_k10": ("both", 10, 1.0, 1.0, True, True),
    "hybrid_k30": ("both", 30, 1.0, 1.0, True, True),
    "hybrid_k60 (default)": ("both", 60, 1.0, 1.0, True, True),
    "hybrid_k100": ("both", 100, 1.0, 1.0, True, True),
    "hybrid_dense_x2": ("both", 60, 2.0, 1.0, True, True),
    "hybrid_bm25_x2": ("both", 60, 1.0, 2.0, True, True),
    "hybrid_no_diversity": ("both", 60, 1.0, 1.0, False, True),
}
DEFAULT = "hybrid_k60 (default)"


def fuse(dense, lexical, k, dense_weight, lexical_weight, diversify):
    fused: dict[str, dict] = {}
    for weight, hits, rank_key in (
        (dense_weight, dense, "dense_rank"),
        (lexical_weight, lexical, "lexical_rank"),
    ):
        if weight == 0:
            continue
        for rank, hit in enumerate(hits, 1):
            item = fused.setdefault(hit["chunk_id"], {**hit, "score": 0.0})
            item[rank_key] = rank
            item["score"] += weight / (k + rank)
    ranked = sorted(
        (Retriever._normalize_metadata(i) for i in fused.values()),
        key=lambda i: i["score"],
        reverse=True,
    )
    return Retriever._diversify(ranked) if diversify else ranked


def bootstrap(values, rng):
    values = np.asarray(values, dtype=float)
    means = values[rng.integers(0, len(values), (RESAMPLES, len(values)))].mean(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def paired(a, b, rng):
    diff = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    null = (rng.choice([-1.0, 1.0], (RESAMPLES, len(diff))) * diff).mean(axis=1)
    return {
        "mean_diff": float(diff.mean()),
        "ci95": bootstrap(diff, rng),
        "p_value": float(
            (np.sum(np.abs(null) >= abs(diff.mean()) - 1e-12) + 1) / (RESAMPLES + 1)
        ),
    }


def main() -> None:
    settings = get_settings()
    database, vectors = Database(settings.sqlite_path), VectorStore(settings)
    cloud = CloudModels(settings.model_copy(update={"openai_api_key": ""}))
    rng = np.random.default_rng(SEED)
    summary: dict = {
        "index": settings.collection_prefix,
        "variants": {},
        "reproduction": {},
    }
    rows = []
    for strategy in sys.argv[1:] or ("window", "heading"):
        recorded = [
            json.loads(line)
            for line in (RUN / f"{strategy}_rerank_off.jsonl")
            .read_text("utf-8")
            .splitlines()
        ]
        recorded.sort(key=lambda r: r["id"])
        per_variant: dict[str, list[dict]] = {name: [] for name in VARIANTS}
        same_rank = same_top10 = 0
        for record in recorded:
            query_vector = cloud.embed_query(record["question"])
            dense = vectors.search(strategy, query_vector, settings.dense_limit)
            lexical = {
                True: database.lexical_search(
                    record["lexical_query"], strategy, settings.lexical_limit
                ),
                False: database.lexical_search(
                    record["question"], strategy, settings.lexical_limit
                ),
            }
            for name, (channels, k, wd, wl, diversify, translated) in VARIANTS.items():
                hits = fuse(
                    dense if channels in ("dense", "both") else [],
                    lexical[translated] if channels in ("lexical", "both") else [],
                    k,
                    wd if channels != "lexical" else 0,
                    wl if channels != "dense" else 0,
                    diversify,
                )[:TOP]
                urls = [hit["url"] for hit in hits]
                rank = _rank_of_relevant(urls, record["relevant_urls"])
                result = {
                    "rank": rank,
                    "rr": 1 / rank if rank and rank <= TOP else 0.0,
                    "recall5": _recall(urls, record["relevant_urls"], 5),
                    "recall10": _recall(urls, record["relevant_urls"], 10),
                    "hit5": float(bool(rank and rank <= 5)),
                }
                per_variant[name].append(result)
                rows.append(
                    {
                        "strategy": strategy,
                        "variant": name,
                        "id": record["id"],
                        "rank": rank or "",
                    }
                )
                if name == DEFAULT:
                    same_rank += rank == record["rank"]
                    same_top10 += [h["chunk_id"] for h in hits] == [
                        h["chunk_id"] for h in record["hits"]
                    ]
        summary["reproduction"][strategy] = {
            "questions": len(recorded),
            "same_first_relevant_rank_as_benchmark": same_rank,
            "identical_top10_chunk_lists": same_top10,
        }
        base = per_variant[DEFAULT]
        for name, results in per_variant.items():
            entry = {}
            for metric, key in (
                ("recall_at_5", "recall5"),
                ("recall_at_10", "recall10"),
                ("mrr_at_10", "rr"),
                ("hit_rate_at_5", "hit5"),
            ):
                values = [r[key] for r in results]
                entry[metric] = {
                    "mean": float(np.mean(values)),
                    "ci95": bootstrap(values, rng),
                }
            if name != DEFAULT:
                entry["vs_default_mrr"] = paired(
                    [r["rr"] for r in base], [r["rr"] for r in results], rng
                )
                entry["vs_default_recall5"] = paired(
                    [r["recall5"] for r in base], [r["recall5"] for r in results], rng
                )
            summary["variants"].setdefault(strategy, {})[name] = entry
        print(strategy, json.dumps(summary["reproduction"][strategy]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (OUT / "per_question.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["strategy", "variant", "id", "rank"])
        writer.writeheader()
        writer.writerows(rows)
    for strategy, variants in summary["variants"].items():
        print(f"\n{strategy}")
        for name, entry in variants.items():
            extra = entry.get("vs_default_mrr")
            print(
                f"  {name:28s} R@5 {entry['recall_at_5']['mean']:.3f}  R@10 {entry['recall_at_10']['mean']:.3f}  "
                f"MRR {entry['mrr_at_10']['mean']:.3f}"
                + (
                    f"  dMRR {extra['mean_diff']:+.3f} p={extra['p_value']:.3f}"
                    if extra
                    else ""
                )
            )


if __name__ == "__main__":
    main()
