from pathlib import Path

from app.database import Database


def test_lexical_search(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    page = {
        "doc_id": "pods.md",
        "source_path": "pods.md",
        "url": "https://kubernetes.io/docs/pods/",
        "title": "Pods",
    }
    database.replace_document(
        page=page,
        strategy="heading",
        content_hash="abc",
        git_commit="123",
        chunks=[
            {
                "chunk_id": "one",
                "url": page["url"],
                "heading": "Lifecycle",
                "content": "A Pod has a lifecycle and restart policy.",
            }
        ],
    )
    hits = database.lexical_search("pod lifecycle", "heading", 5)
    assert hits[0]["chunk_id"] == "one"


def test_lexical_query_cache(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.db")
    assert database.cached_lexical_query("вопрос") is None
    database.cache_lexical_query("вопрос", "question")
    assert database.cached_lexical_query("вопрос") == "question"
