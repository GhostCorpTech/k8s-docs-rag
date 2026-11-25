"""Verify a restored (or freshly built) index without any LLM calls.

Checks, per strategy:
  1. every page hash in SQLite equals the hash recomputed from the documentation source,
     chunker version and embedding identity (the same identity the evaluation pins);
  2. the set of chunk IDs in Qdrant equals the set in SQLite FTS5;
  3. for a random sample, re-embedding the stored chunk text with the local model
     reproduces the stored vector (cosine similarity).
"""

import random

import numpy as np

from .cloud import CloudModels
from .config import Settings
from .database import Database
from .docs import current_commit, load_pages
from .evaluation import _digest
from .indexer import CHUNKER_VERSION, index_hash
from .vector_store import VectorStore


def _qdrant_points(store: VectorStore, strategy: str, with_vectors: bool = False) -> dict:
    points, offset = {}, None
    while True:
        batch, offset = store.client.scroll(
            collection_name=store.settings.collection(strategy),
            limit=1_000,
            offset=offset,
            with_payload=["content"] if with_vectors else False,
            with_vectors=with_vectors,
        )
        for point in batch:
            points[str(point.id)] = point
        if offset is None:
            return points


def verify_index(settings: Settings, sample: int = 100, seed: int = 0) -> dict:
    try:
        pages = load_pages(settings.docs_repo_path)
    except FileNotFoundError:
        pages = None  # e.g. right after restore-index, before `index --sync`
    database = Database(settings.sqlite_path)
    store = VectorStore(settings)
    cloud = CloudModels(settings.model_copy(update={"openai_api_key": "", "embedding_api_key": ""}))
    report: dict = {
        "source_commit": current_commit(settings.docs_repo_path) if pages is not None else None,
        "chunker_version": CHUNKER_VERSION,
        "embedding": settings.embedding_identity(),
        "strategies": {},
    }
    rng = random.Random(seed)
    for strategy in ("heading", "window"):
        actual = database.page_hashes(strategy)
        expected = (
            {
                doc_id: index_hash(page.content_hash, strategy, settings.embedding_identity())
                for doc_id, page in pages.items()
            }
            if pages is not None
            else None
        )
        with database.connect() as connection:
            sqlite_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT chunk_id FROM chunks_fts WHERE strategy = ?", (strategy,)
                )
            }
        points = _qdrant_points(store, strategy, with_vectors=sample > 0)
        entry = {
            "pages_expected": len(expected) if expected is not None else None,
            "pages_indexed": len(actual),
            "page_hashes_match_source": actual == expected
            if expected is not None
            else "skipped: no docs checkout",
            "page_hashes_sha256": _digest(actual),
            "chunks_sqlite": len(sqlite_ids),
            "points_qdrant": len(points),
            "chunk_ids_match": sqlite_ids == set(points),
        }
        if sample > 0 and points:
            norms = np.array(
                [
                    np.linalg.norm(np.asarray(point.vector, dtype=np.float32))
                    for point in points.values()
                ]
            )
            entry["vectors_not_unit_norm"] = int(np.sum(np.abs(norms - 1) > 1e-3))
            chosen = rng.sample(sorted(points), min(sample, len(points)))
            stored = np.array([points[i].vector for i in chosen], dtype=np.float32)
            fresh = np.array(
                cloud.embed_documents([points[i].payload["content"] for i in chosen]),
                dtype=np.float32,
            )
            denominator = np.linalg.norm(stored, axis=1) * np.linalg.norm(fresh, axis=1)
            cosine = np.divide(
                (stored * fresh).sum(axis=1),
                denominator,
                out=np.zeros(len(chosen), dtype=np.float32),
                where=denominator > 0,
            )
            entry["vector_sample"] = {
                "size": len(chosen),
                "min_cosine": float(cosine.min()),
                "mean_cosine": float(cosine.mean()),
                "max_abs_component_diff": float(np.abs(stored - fresh).max()),
            }
        entry["ok"] = bool(
            entry["page_hashes_match_source"] in (True, "skipped: no docs checkout")
            and entry["chunk_ids_match"]
            and entry.get("vectors_not_unit_norm", 0) == 0
            and entry.get("vector_sample", {}).get("min_cosine", 1.0) > 0.999
        )
        report["strategies"][strategy] = entry
    report["ok"] = all(item["ok"] for item in report["strategies"].values())
    return report


def repair_vectors(settings: Settings, strategy: str, batch_size: int = 32, log=print) -> dict:
    """Re-embed points whose stored vector is not unit-norm (e.g. zeros after storage
    corruption) from the chunk text kept in the payload. IDs, payloads, chunking and the
    page-hash identity are untouched. Resumable: already repaired points are skipped."""
    from qdrant_client import models

    store = VectorStore(settings)
    cloud = CloudModels(settings.model_copy(update={"openai_api_key": "", "embedding_api_key": ""}))
    points = _qdrant_points(store, strategy, with_vectors=True)
    broken = sorted(
        point_id
        for point_id, point in points.items()
        if abs(float(np.linalg.norm(np.asarray(point.vector, dtype=np.float32))) - 1) > 1e-3
    )
    log(f"[{strategy}] {len(broken)} of {len(points)} vectors need repair")
    for start in range(0, len(broken), batch_size):
        batch = broken[start : start + batch_size]
        vectors = cloud.embed_documents([points[i].payload["content"] for i in batch])
        store.client.update_vectors(
            collection_name=settings.collection(strategy),
            points=[
                models.PointVectors(id=point_id, vector=vector)
                for point_id, vector in zip(batch, vectors, strict=True)
            ],
            wait=True,
        )
        log(f"[{strategy}] repaired {min(start + batch_size, len(broken))}/{len(broken)}")
    return {"strategy": strategy, "points": len(points), "repaired": len(broken)}
