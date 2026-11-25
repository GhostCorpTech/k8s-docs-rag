import re
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urldefrag

from openai import APIStatusError

from .cloud import CloudModels
from .config import Settings
from .database import Database
from .vector_store import VectorStore

RERANK_CANDIDATES = 20


@dataclass
class RetrievalTrace:
    """What actually happened during one retrieval, surfaced to the API and the UI."""

    strategy: str
    lexical_query: str | None = None
    dense_candidates: int = 0
    lexical_candidates: int = 0
    fused_candidates: int = 0
    rerank_requested: bool = False
    rerank_applied: bool = False
    degraded: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _is_fatal(error: Exception) -> bool:
    """Auth/billing problems must surface; degrading would hide a broken deployment."""
    return isinstance(error, APIStatusError) and error.status_code in {401, 402, 403}


class Retriever:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings.sqlite_path)
        self.vectors = VectorStore(settings)

    def search(
        self,
        query: str,
        strategy: str = "heading",
        *,
        rerank: bool = True,
        limit: int | None = None,
        models: CloudModels | None = None,
    ) -> list[dict]:
        """Strict retrieval used by evaluation: any provider failure raises."""
        hits, _ = self.search_with_trace(
            query, strategy, rerank=rerank, limit=limit, models=models, strict=True
        )
        return hits

    def search_with_trace(
        self,
        query: str,
        strategy: str = "heading",
        *,
        rerank: bool = True,
        limit: int | None = None,
        models: CloudModels | None = None,
        strict: bool = False,
    ) -> tuple[list[dict], RetrievalTrace]:
        """Hybrid dense + BM25 retrieval fused with RRF, optionally reranked by an LLM.

        With ``strict=False`` (interactive chat) a failing translation falls back to the
        original query for BM25 and a failing reranker falls back to the RRF order. Both
        degradations are reported in the trace instead of being hidden. Evaluation always
        runs strict so that a provider error can never silently change a metric.
        """
        cloud = models or CloudModels(self.settings)
        trace = RetrievalTrace(strategy=strategy, rerank_requested=rerank)

        started = time.perf_counter()
        query_vector = cloud.embed_query(query)
        trace.timings_ms["embed"] = _elapsed(started)

        started = time.perf_counter()
        dense = self.vectors.search(strategy, query_vector, self.settings.dense_limit)
        trace.timings_ms["dense"] = _elapsed(started)

        started = time.perf_counter()
        lexical_query = self.database.cached_lexical_query(query)
        if lexical_query is None:
            try:
                lexical_query = cloud.lexical_query(query)
                self.database.cache_lexical_query(query, lexical_query)
            except Exception as error:
                if strict or _is_fatal(error):
                    raise
                lexical_query = query
                trace.degraded.append(f"translation_failed:{type(error).__name__}")
            trace.timings_ms["translate"] = _elapsed(started)
        trace.lexical_query = lexical_query

        started = time.perf_counter()
        lexical = self.database.lexical_search(lexical_query, strategy, self.settings.lexical_limit)
        trace.timings_ms["bm25"] = _elapsed(started)

        candidates = self.fuse(dense, lexical)
        trace.dense_candidates = len(dense)
        trace.lexical_candidates = len(lexical)
        trace.fused_candidates = len(candidates)

        target_limit = limit or self.settings.final_limit
        if rerank and candidates:
            started = time.perf_counter()
            try:
                candidates = cloud.rerank(query, candidates[:RERANK_CANDIDATES])
                trace.rerank_applied = True
            except Exception as error:
                if strict or _is_fatal(error):
                    raise
                trace.degraded.append(f"rerank_failed:{type(error).__name__}")
            trace.timings_ms["rerank"] = _elapsed(started)
        return candidates[:target_limit], trace

    def fuse(self, dense: list[dict], lexical: list[dict]) -> list[dict]:
        """Weighted Reciprocal Rank Fusion, then metadata cleanup and per-page diversity."""
        fused: dict[str, dict] = {}
        for rank, hit in enumerate(dense, 1):
            chunk_id = hit.get("chunk_id")
            if not chunk_id:
                continue
            item = fused.setdefault(chunk_id, {**hit, "score": 0.0})
            item["dense_rank"] = rank
            item["score"] += self.settings.rrf_dense_weight / (self.settings.rrf_k + rank)

        for rank, hit in enumerate(lexical, 1):
            chunk_id = hit["chunk_id"]
            item = fused.setdefault(chunk_id, {**hit, "score": 0.0})
            item["lexical_rank"] = rank
            item["score"] += self.settings.rrf_lexical_weight / (self.settings.rrf_k + rank)

        normalized = [self._normalize_metadata(item) for item in fused.values()]
        return self._diversify(sorted(normalized, key=lambda item: item["score"], reverse=True))

    @staticmethod
    def _normalize_metadata(candidate: dict) -> dict:
        """Resolve Hugo explicit heading anchors without re-embedding stored text."""
        item = dict(candidate)
        match = re.search(r"\s*\{#([^}]+)}\s*$", item["heading"])
        if match:
            item["heading"] = item["heading"][: match.start()].rstrip()
            base_url = urldefrag(item["url"])[0]
            item["url"] = f"{base_url}#{match.group(1)}"
        return item

    @staticmethod
    def _diversify(candidates: list[dict], max_per_document: int = 2) -> list[dict]:
        """Prevent a large page or repeated anchor from monopolizing candidates."""
        output: list[dict] = []
        seen_urls: set[str] = set()
        document_counts: dict[str, int] = {}
        for candidate in candidates:
            url = candidate["url"]
            doc_id = candidate["doc_id"]
            if url in seen_urls or document_counts.get(doc_id, 0) >= max_per_document:
                continue
            seen_urls.add(url)
            document_counts[doc_id] = document_counts.get(doc_id, 0) + 1
            output.append(candidate)
        return output


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
