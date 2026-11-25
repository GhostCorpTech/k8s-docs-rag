import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(
    os.environ.get("RAG_PROJECT_ROOT")
    or (_PACKAGE_ROOT.parent if _PACKAGE_ROOT.name == "backend" else _PACKAGE_ROOT)
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    chat_model: str = "gpt-4.1-mini"
    embedding_provider: Literal["local", "openai"] = "local"
    local_embedding_model: str = "intfloat/multilingual-e5-base"
    model_cache_path: Path = PROJECT_ROOT / "data" / "models"
    local_embedding_batch_size: int = Field(default=4, ge=1, le=32)
    local_embedding_threads: int = Field(default=6, ge=1, le=32)
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"
    rerank_model: str = "gpt-4.1-mini"
    judge_model: str = "gpt-4.1-mini"
    api_response_log_path: Path | None = None
    api_pricing_path: Path = PROJECT_ROOT / "data" / "billing" / "pricing.json"
    live_api_log_path: Path = PROJECT_ROOT / "data" / "diagnostics" / "live-api.jsonl"
    api_log_content: bool = True

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    sqlite_path: Path = PROJECT_ROOT / "data" / "rag.db"
    docs_repo_path: Path = PROJECT_ROOT / "data" / "kubernetes-website"
    eval_path: Path = PROJECT_ROOT / "eval" / "questions.jsonl"

    dense_limit: int = Field(default=30, ge=1, le=100)
    lexical_limit: int = Field(default=30, ge=1, le=100)
    final_limit: int = Field(default=8, ge=1, le=30)
    rrf_k: int = Field(default=60, ge=1)
    rrf_dense_weight: float = Field(default=1.0, ge=0)
    rrf_lexical_weight: float = Field(default=1.0, ge=0)
    max_context_chars: int = Field(default=30_000, ge=1_000)

    docs_git_url: str = "https://github.com/kubernetes/website.git"
    docs_git_branch: str = "main"
    collection_prefix: str = "kubernetes_docs"

    def model_post_init(self, _context: object, /) -> None:
        # Relative paths are always rooted at the repository, not at the caller's cwd.
        for field_name in (
            "sqlite_path",
            "docs_repo_path",
            "eval_path",
            "model_cache_path",
            "api_pricing_path",
            "live_api_log_path",
        ):
            value = getattr(self, field_name)
            if not value.is_absolute():
                setattr(self, field_name, PROJECT_ROOT / value)

    def collection(self, strategy: str) -> str:
        return f"{self.collection_prefix}_{strategy}"

    def active_embedding_model(self) -> str:
        if self.embedding_provider == "local":
            return self.local_embedding_model
        return self.embedding_model

    def embedding_identity(self) -> str:
        endpoint = (
            (self.embedding_base_url or self.openai_base_url)
            if self.embedding_provider == "openai"
            else "local"
        )
        return f"{self.embedding_provider}:{endpoint}:{self.active_embedding_model()}"

    def ensure_local_dirs(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.docs_repo_path.parent.mkdir(parents=True, exist_ok=True)
        self.eval_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_cache_path.mkdir(parents=True, exist_ok=True)
        (PROJECT_ROOT / ".tmp").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_local_dirs()
    return settings
