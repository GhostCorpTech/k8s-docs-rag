import hashlib
from dataclasses import dataclass
from typing import Literal

from rich.console import Console

from .cloud import CloudModels
from .config import Settings
from .database import Database
from .docs import Chunker, current_commit, load_pages, sync_repository
from .local_embeddings import get_local_model
from .token_budget import TokenBudget
from .vector_store import VectorStore

console = Console()
CHUNKER_VERSION = "2026-11-v4-token-aware"


def index_hash(content_hash: str, strategy: str, embedding_model: str) -> str:
    value = f"{content_hash}:{strategy}:{CHUNKER_VERSION}:{embedding_model}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class IndexStats:
    strategy: str
    total_pages: int
    added: int
    changed: int
    deleted: int
    unchanged: int
    chunks_written: int
    git_commit: str
    dry_run: bool


class Indexer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings.sqlite_path)
        self.chunker = Chunker()

    def sync(self) -> str:
        return sync_repository(
            self.settings.docs_repo_path,
            self.settings.docs_git_url,
            self.settings.docs_git_branch,
        )

    def run(self, strategy: Literal["heading", "window"], *, dry_run: bool = False) -> IndexStats:
        pages = load_pages(self.settings.docs_repo_path)
        commit = current_commit(self.settings.docs_repo_path)
        existing = self.database.page_hashes(strategy)
        current_ids = set(pages)
        existing_ids = set(existing)
        added_ids = current_ids - existing_ids
        deleted_ids = existing_ids - current_ids
        changed_ids = {
            doc_id
            for doc_id in current_ids & existing_ids
            if index_hash(
                pages[doc_id].content_hash,
                strategy,
                self.settings.embedding_identity(),
            )
            != existing[doc_id]
        }
        pending_ids = sorted(added_ids | changed_ids)

        stats = IndexStats(
            strategy=strategy,
            total_pages=len(pages),
            added=len(added_ids),
            changed=len(changed_ids),
            deleted=len(deleted_ids),
            unchanged=len(current_ids) - len(pending_ids),
            chunks_written=0,
            git_commit=commit,
            dry_run=dry_run,
        )
        if dry_run:
            return stats

        vectors_store = VectorStore(self.settings)
        cloud = CloudModels(self.settings) if pending_ids else None
        if pending_ids:
            if self.settings.embedding_provider == "local":
                get_local_model(
                    self.settings.local_embedding_model,
                    str(self.settings.model_cache_path),
                    self.settings.local_embedding_threads,
                )
            self.chunker = Chunker(
                TokenBudget.from_cache(
                    self.settings.model_cache_path, self.settings.local_embedding_model
                )
            )
        for doc_id in sorted(deleted_ids):
            vectors_store.delete_document(strategy, doc_id)
            self.database.delete_document(doc_id, strategy)

        for position, doc_id in enumerate(pending_ids, 1):
            page = pages[doc_id]
            chunks = self.chunker.chunk(page, strategy)
            if not chunks:
                vectors_store.delete_document(strategy, doc_id)
                self.database.replace_document(
                    page=page.as_dict(),
                    strategy=strategy,
                    content_hash=index_hash(
                        page.content_hash, strategy, self.settings.embedding_identity()
                    ),
                    git_commit=commit,
                    chunks=[],
                )
                continue
            enriched = [{**chunk, "doc_id": page.doc_id, "title": page.title} for chunk in chunks]
            vectors: list[list[float]] = []
            for start in range(0, len(enriched), 64):
                batch = enriched[start : start + 64]
                vectors.extend(
                    cloud.embed_documents([chunk["content"] for chunk in batch])  # type: ignore[union-attr]
                )

            # Qdrant first: SQLite state is committed only after all remote writes succeeded.
            vectors_store.delete_document(strategy, doc_id)
            vectors_store.upsert(strategy, enriched, vectors)
            self.database.replace_document(
                page=page.as_dict(),
                strategy=strategy,
                content_hash=index_hash(
                    page.content_hash, strategy, self.settings.embedding_identity()
                ),
                git_commit=commit,
                chunks=enriched,
            )
            stats.chunks_written += len(chunks)
            console.print(
                f"[{strategy}] {position}/{len(pending_ids)} {doc_id} ({len(chunks)} chunks)"
            )
        return stats
