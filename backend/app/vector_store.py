from qdrant_client import QdrantClient, models

from .config import Settings


class VectorStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=60,
        )

    def exists(self, strategy: str) -> bool:
        return self.client.collection_exists(self.settings.collection(strategy))

    def ensure_collection(self, strategy: str, vector_size: int) -> None:
        name = self.settings.collection(strategy)
        if self.client.collection_exists(name):
            info = self.client.get_collection(name)
            vectors = info.config.params.vectors
            existing_size = getattr(vectors, "size", None)
            if existing_size is not None and existing_size != vector_size:
                raise RuntimeError(
                    f"Collection {name} has vector size {existing_size}, but API returned "
                    f"{vector_size}. Change collection_prefix or recreate the collection."
                )
            return
        self.client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )
        self.client.create_payload_index(
            collection_name=name,
            field_name="doc_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )

    def delete_document(self, strategy: str, doc_id: str) -> None:
        if not self.exists(strategy):
            return
        self.client.delete(
            collection_name=self.settings.collection(strategy),
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))
                    ]
                )
            ),
            wait=True,
        )

    def upsert(self, strategy: str, chunks: list[dict], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        self.ensure_collection(strategy, len(vectors[0]))
        points = [
            models.PointStruct(
                id=chunk["chunk_id"],
                vector=vector,
                payload={
                    "chunk_id": chunk["chunk_id"],
                    "doc_id": chunk["doc_id"],
                    "title": chunk["title"],
                    "heading": chunk["heading"],
                    "url": chunk["url"],
                    "content": chunk["content"],
                    "embedding_tokens": chunk.get("embedding_tokens"),
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        for start in range(0, len(points), 128):
            self.client.upsert(
                collection_name=self.settings.collection(strategy),
                points=points[start : start + 128],
                wait=True,
            )

    def search(self, strategy: str, vector: list[float], limit: int) -> list[dict]:
        if not self.exists(strategy):
            return []
        response = self.client.query_points(
            collection_name=self.settings.collection(strategy),
            query=vector,
            limit=limit,
            with_payload=True,
        )
        output: list[dict] = []
        for point in response.points:
            payload = dict(point.payload or {})
            payload["dense_score"] = float(point.score)
            output.append(payload)
        return output
