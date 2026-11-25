from pathlib import Path

import pytest

from app.config import Settings
from app.database import Database
from app.docs import load_pages
from app.evaluation import run_evaluation


def test_evaluation_rejects_incomplete_indexes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    page = repo / "content" / "en" / "docs" / "example.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntitle: Example\n---\nBody", encoding="utf-8")
    eval_path = tmp_path / "eval" / "questions.jsonl"
    eval_path.parent.mkdir()
    eval_path.write_text("", encoding="utf-8")
    settings = Settings(
        sqlite_path=tmp_path / "rag.db",
        docs_repo_path=repo,
        eval_path=eval_path,
        model_cache_path=tmp_path / "models",
    )

    with pytest.raises(RuntimeError, match="complete indexes"):
        run_evaluation(settings)


def test_matching_page_count_is_not_enough_for_evaluation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    page = repo / "content/en/docs/example.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntitle: Example\n---\nNew source", encoding="utf-8")
    settings = Settings(_env_file=None, sqlite_path=tmp_path / "rag.db", docs_repo_path=repo)
    database = Database(settings.sqlite_path)
    source = load_pages(repo)["example.md"]
    for strategy in ("heading", "window"):
        database.replace_document(
            page=source.as_dict(),
            strategy=strategy,
            content_hash="stale-hash",
            git_commit="old",
            chunks=[],
        )
    with pytest.raises(RuntimeError, match="matching current source"):
        run_evaluation(settings)
