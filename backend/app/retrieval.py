import re
from urllib.parse import urldefrag

from .cloud import CloudModels
from .config import Settings
from .database import Database
from .vector_store import VectorStore


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
        cloud = models or CloudModels(self.settings)
        query_vector = cloud.embed_query(query)
        dense = self.vectors.search(strategy, query_vector, self.settings.dense_limit)
        lexical_query = self.database.cached_lexical_query(query)
        if lexical_query is None:
            lexical_query = cloud.lexical_query(query)
            self.database.cache_lexical_query(query, lexical_query)
        lexical = self.database.lexical_search(lexical_query, strategy, self.settings.lexical_limit)

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
        candidates = self._diversify(
            sorted(normalized, key=lambda item: item["score"], reverse=True)
        )
        target_limit = limit or self.settings.final_limit
        if rerank:
            candidates = cloud.rerank(query, candidates[:20])
        return candidates[:target_limit]

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
