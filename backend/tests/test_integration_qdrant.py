"""End-to-end retrieval against a real Qdrant and a real SQLite FTS5 index.

Runs when QDRANT_TEST_URL points to a Qdrant server (CI starts one as a service);
skipped otherwise. Embeddings are a deterministic bag-of-words hash, so the test needs
no model download and no network beyond Qdrant.
"""

import hashlib
import os
import uuid
from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.database import Database
from app.retrieval import Retriever
from app.vector_store import VectorStore
from app.verification import repair_vectors

QDRANT_URL = os.environ.get("QDRANT_TEST_URL")
pytestmark = pytest.mark.skipif(not QDRANT_URL, reason="QDRANT_TEST_URL not set")
DIM = 64

DOCS = {
    "pods.md": ("Pods", "A Pod is the smallest deployable unit; it groups containers."),
    "deployment.md": ("Deployments", "A Deployment rolls out ReplicaSets and supports rollback."),
    "probes.md": ("Probes", "Liveness probes restart a container; readiness probes gate traffic."),
    "secrets.md": (
        "Secrets",
        "Secrets are stored unencrypted in etcd unless encryption is enabled.",
    ),
}


def embed(text: str) -> list[float]:
    vector = np.zeros(DIM, dtype=np.float32)
    for token in text.lower().replace(";", " ").replace(".", " ").split():
        vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM] += 1
    return (vector / np.linalg.norm(vector)).tolist()


class HashCloud:
    def embed_query(self, query: str) -> list[float]:
        return embed(query)

    def embed_documents(self, texts):
        return [embed(text) for text in texts]

    def lexical_query(self, query: str) -> str:
        return query

    def quick_lexical_query(self, query: str, timeout: float) -> str:
        return query

    def rerank(self, query, hits):
        return hits


@pytest.fixture
def indexed(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        qdrant_url=QDRANT_URL,
        sqlite_path=tmp_path / "rag.db",
        collection_prefix=f"test_{uuid.uuid4().hex[:8]}",
    )
    store, database = VectorStore(settings), Database(settings.sqlite_path)
    for doc_id, (title, text) in DOCS.items():
        chunk = {
            "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id)),
            "doc_id": doc_id,
            "title": title,
            "heading": title,
            "url": f"https://kubernetes.io/docs/{doc_id[:-3]}/",
            "content": f"# {title}\n{text}",
        }
        store.upsert("window", [chunk], [embed(chunk["content"])])
        page = {"doc_id": doc_id, "source_path": doc_id, "url": chunk["url"], "title": title}
        database.replace_document(
            page=page, strategy="window", content_hash=doc_id, git_commit="test", chunks=[chunk]
        )
    yield settings, store
    store.client.delete_collection(settings.collection("window"))


def test_hybrid_search_finds_the_right_page_in_both_channels(indexed) -> None:
    settings, _ = indexed
    hits, trace = Retriever(settings).search_with_trace(
        "readiness probes traffic", "window", rerank=False, models=HashCloud()
    )
    assert hits[0]["doc_id"] == "probes.md"
    assert hits[0]["dense_rank"] == 1 and hits[0]["lexical_rank"] == 1
    assert trace.dense_candidates == 4 and trace.fused_candidates == 4


def test_deleting_a_document_removes_it_from_qdrant(indexed) -> None:
    settings, store = indexed
    store.delete_document("window", "secrets.md")
    count = store.client.count(settings.collection("window"), exact=True).count
    assert count == 3


def test_zero_vectors_are_detected_and_repaired(indexed, monkeypatch) -> None:
    from qdrant_client import models

    settings, store = indexed
    broken = str(uuid.uuid5(uuid.NAMESPACE_URL, "pods.md"))
    store.client.update_vectors(
        settings.collection("window"),
        points=[models.PointVectors(id=broken, vector=[0.0] * DIM)],
        wait=True,
    )
    monkeypatch.setattr("app.verification.CloudModels", lambda _settings: HashCloud())
    result = repair_vectors(settings, "window", log=lambda *_: None)
    assert result == {"strategy": "window", "points": 4, "repaired": 1}
    point = store.client.retrieve(settings.collection("window"), [broken], with_vectors=True)[0]
    assert np.isclose(np.linalg.norm(point.vector), 1.0, atol=1e-5)


def test_export_and_restore_roundtrip(indexed, tmp_path) -> None:
    from app.snapshot import export_index, restore_index

    settings, store = indexed
    for strategy in ("heading",):  # export expects both strategies to exist
        store.upsert(
            strategy,
            [
                {
                    "chunk_id": str(uuid.uuid4()),
                    "doc_id": "x.md",
                    "title": "X",
                    "heading": "X",
                    "url": "https://kubernetes.io/docs/x/",
                    "content": "x",
                }
            ],
            [embed("x")],
        )
    archive = tmp_path / "index.zip"
    manifest = export_index(settings, archive)
    assert manifest["collections"]["window"]["points"] == 4

    target = settings.model_copy(
        update={
            "collection_prefix": settings.collection_prefix + "_r",
            "sqlite_path": tmp_path / "restored.db",
        }
    )
    try:
        result = restore_index(target, archive)
        assert result["index_status"]["window"]["chunks"] == 4
        assert VectorStore(target).client.count(target.collection("window"), exact=True).count == 4
        with pytest.raises(RuntimeError, match="already exists"):
            restore_index(target, archive)
    finally:
        for strategy in ("heading", "window"):
            store.client.delete_collection(target.collection(strategy))
            store.client.delete_collection(settings.collection(strategy))
