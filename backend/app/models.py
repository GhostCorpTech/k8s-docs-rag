from typing import Literal

from pydantic import BaseModel, Field

Strategy = Literal["heading", "window"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2_000)
    strategy: Strategy = "heading"
    rerank: bool = True
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


class SearchResponse(BaseModel):
    query: str
    strategy: Strategy
    reranked: bool
    hits: list[SearchHit]


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4_000)
    strategy: Strategy = "heading"
    rerank: bool = True
    limit: int = Field(default=8, ge=1, le=20)


class Citation(BaseModel):
    index: int
    title: str
    heading: str
    url: str
    excerpt: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    strategy: Strategy
    reranked: bool
