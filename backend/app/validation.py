from .config import Settings
from .docs import Chunker, current_commit, load_pages
from .indexer import CHUNKER_VERSION
from .token_budget import TokenBudget


def validate_chunks(settings: Settings) -> dict:
    """Exercise every source page with the cached tokenizer, without embeddings/API calls."""
    pages = load_pages(settings.docs_repo_path)
    chunker = Chunker(
        TokenBudget.from_cache(settings.model_cache_path, settings.local_embedding_model)
    )
    counts = {}
    for strategy in ("heading", "window"):
        total, maximum, oversized = 0, 0, 0
        for page in pages.values():
            for chunk in chunker.chunk(page, strategy):
                count = chunker.budget.count(chunk["content"])
                total += 1
                maximum = max(maximum, count)
                oversized += count > 512
        counts[strategy] = {
            "pages": len(pages),
            "chunks": total,
            "max_embedding_tokens": maximum,
            "oversized_chunks": oversized,
        }
    if any(item["oversized_chunks"] for item in counts.values()):
        raise RuntimeError("Chunking validation failed: embedding inputs exceed 512 tokens")
    return {
        "source_commit": current_commit(settings.docs_repo_path),
        "chunker_version": CHUNKER_VERSION,
        "counts": counts,
    }
