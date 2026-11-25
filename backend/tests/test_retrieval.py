import httpx
import pytest
from openai import APIStatusError

from app.config import Settings
from app.retrieval import Retriever


def test_diversify_removes_duplicate_urls_and_caps_documents() -> None:
    candidates = [
        {"chunk_id": "1", "doc_id": "large", "url": "https://docs/page/#same"},
        {"chunk_id": "2", "doc_id": "large", "url": "https://docs/page/#same"},
        {"chunk_id": "3", "doc_id": "large", "url": "https://docs/page/#other"},
        {"chunk_id": "4", "doc_id": "large", "url": "https://docs/page/#third"},
        {"chunk_id": "5", "doc_id": "small", "url": "https://docs/other/"},
    ]

    result = Retriever._diversify(candidates)

    assert [item["chunk_id"] for item in result] == ["1", "3", "5"]


def test_normalize_metadata_uses_explicit_hugo_anchor() -> None:
    hit = {
        "heading": "Parent > Resource {#networkpolicy-resource}",
        "url": "https://kubernetes.io/docs/page/#resource-networkpolicy-resource",
    }

    result = Retriever._normalize_metadata(hit)

    assert result["heading"] == "Parent > Resource"
    assert result["url"] == "https://kubernetes.io/docs/page/#networkpolicy-resource"


# --- Hybrid search, fusion and graceful degradation ------------------------------------


def _hit(chunk_id: str, doc_id: str | None = None) -> dict:
    doc = doc_id or chunk_id
    return {
        "chunk_id": chunk_id,
        "doc_id": doc,
        "title": doc.title(),
        "heading": doc.title(),
        "url": f"https://kubernetes.io/docs/{doc}/",
        "content": f"content of {chunk_id}",
    }


class FakeDatabase:
    def __init__(self, lexical: list[dict], cached: str | None = None):
        self.lexical, self.cached, self.queries = lexical, cached, []

    def cached_lexical_query(self, query: str) -> str | None:
        return self.cached

    def cache_lexical_query(self, query: str, lexical_query: str) -> None:
        self.cached = lexical_query

    def lexical_search(self, query: str, strategy: str, limit: int) -> list[dict]:
        self.queries.append(query)
        return self.lexical[:limit]


class FakeVectors:
    def __init__(self, dense: list[dict]):
        self.dense = dense

    def search(self, strategy: str, vector: list[float], limit: int) -> list[dict]:
        return self.dense[:limit]


class FakeCloud:
    def __init__(self, *, rerank_error: Exception | None = None, translate_error=None):
        self.rerank_error, self.translate_error = rerank_error, translate_error

    def embed_query(self, query: str) -> list[float]:
        return [0.0]

    def lexical_query(self, query: str) -> str:
        if self.translate_error:
            raise self.translate_error
        return "translated query"

    def rerank(self, query: str, hits: list[dict]) -> list[dict]:
        if self.rerank_error:
            raise self.rerank_error
        return [dict(hit, rerank_score=1 / (i + 1)) for i, hit in enumerate(reversed(hits))]


def make_retriever(dense: list[dict], lexical: list[dict], cached: str | None = "q") -> Retriever:
    retriever = Retriever.__new__(Retriever)
    retriever.settings = Settings(_env_file=None)
    retriever.database = FakeDatabase(lexical, cached)
    retriever.vectors = FakeVectors(dense)
    return retriever


def _status_error(status: int) -> APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "https://router.test"))
    return APIStatusError("failed", response=response, body={})


def test_rrf_rewards_agreement_between_dense_and_lexical() -> None:
    retriever = make_retriever(
        dense=[_hit("a"), _hit("b"), _hit("c")], lexical=[_hit("c"), _hit("d")]
    )
    hits, trace = retriever.search_with_trace("q", rerank=False, models=FakeCloud())
    assert hits[0]["chunk_id"] == "c"  # rank 3 + rank 1 beats rank 1 alone
    assert hits[0]["dense_rank"] == 3 and hits[0]["lexical_rank"] == 1
    assert hits[0]["score"] == pytest.approx(1 / 63 + 1 / 61)
    assert (trace.dense_candidates, trace.lexical_candidates, trace.fused_candidates) == (3, 2, 4)


def test_reranker_failure_degrades_to_rrf_order_in_chat() -> None:
    retriever = make_retriever(dense=[_hit("a"), _hit("b")], lexical=[])
    cloud = FakeCloud(rerank_error=_status_error(400))
    hits, trace = retriever.search_with_trace("q", rerank=True, models=cloud)
    assert [hit["chunk_id"] for hit in hits] == ["a", "b"]
    assert trace.rerank_requested and not trace.rerank_applied
    assert trace.degraded == ["rerank_failed:APIStatusError"]


def test_strict_search_never_hides_reranker_failure() -> None:
    retriever = make_retriever(dense=[_hit("a")], lexical=[])
    with pytest.raises(APIStatusError):
        retriever.search("q", rerank=True, models=FakeCloud(rerank_error=_status_error(400)))


@pytest.mark.parametrize("status", [401, 402, 403])
def test_auth_and_billing_errors_are_never_degraded(status: int) -> None:
    retriever = make_retriever(dense=[_hit("a")], lexical=[])
    cloud = FakeCloud(rerank_error=_status_error(status))
    with pytest.raises(APIStatusError):
        retriever.search_with_trace("q", rerank=True, models=cloud)


def test_translation_failure_falls_back_to_original_query_for_bm25() -> None:
    retriever = make_retriever(dense=[], lexical=[_hit("a")], cached=None)
    cloud = FakeCloud(translate_error=_status_error(500))
    _, trace = retriever.search_with_trace("что такое pod", rerank=False, models=cloud)
    assert retriever.database.queries == ["что такое pod"]
    assert trace.lexical_query == "что такое pod"
    assert trace.degraded == ["translation_failed:APIStatusError"]
    assert retriever.database.cached is None  # a fallback is never cached as a translation


def test_successful_rerank_reorders_and_is_reported() -> None:
    retriever = make_retriever(dense=[_hit("a"), _hit("b")], lexical=[])
    hits, trace = retriever.search_with_trace("q", rerank=True, models=FakeCloud())
    assert [hit["chunk_id"] for hit in hits] == ["b", "a"]
    assert trace.rerank_applied and set(trace.timings_ms) >= {"embed", "dense", "bm25", "rerank"}
