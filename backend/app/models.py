from typing import Literal

from pydantic import BaseModel, Field

Strategy = Literal["heading", "window"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2_000)
    # Benchmark-best configuration (see docs/EVALUATION.md): window chunks, no LLM rerank.
    strategy: Strategy = "window"
    rerank: bool = False
    limit: int = Field(default=8, ge=1, le=20)


class SearchHit(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    heading: str
    url: str
    content: str
    score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None
    rerank_score: float | None = None


class Trace(BaseModel):
    strategy: Strategy
    lexical_query: str | None = None
    dense_candidates: int = 0
    lexical_candidates: int = 0
    fused_candidates: int = 0
    rerank_requested: bool = False
    rerank_applied: bool = False
    degraded: list[str] = []
    timings_ms: dict[str, float] = {}


class SearchResponse(BaseModel):
    query: str
    strategy: Strategy
    reranked: bool
    hits: list[SearchHit]
    trace: Trace | None = None


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4_000)
    # Benchmark-best configuration (see docs/EVALUATION.md): window chunks, no LLM rerank.
    strategy: Strategy = "window"
    rerank: bool = False
    limit: int = Field(default=8, ge=1, le=20)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)


class Citation(BaseModel):
    index: int
    title: str
    heading: str
    url: str
    excerpt: str
    score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    rerank_score: float | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    strategy: Strategy
    reranked: bool
    standalone_question: str | None = None
    trace: Trace | None = None
