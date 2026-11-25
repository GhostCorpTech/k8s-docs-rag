import hashlib
import json
import time
import uuid
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import __version__
from .cloud import CloudModels
from .config import Settings, get_settings
from .database import Database
from .evaluation import LABELS
from .evaluation import _read_records as read_records
from .models import (
    ChatRequest,
    ChatResponse,
    Citation,
    SearchHit,
    SearchRequest,
    SearchResponse,
    Trace,
)
from .retrieval import Retriever

STAGES = {
    "translation": "query translation",
    "condense": "follow-up rewriting",
    "rerank": "reranking",
    "answer": "answer generation",
}


def _live_models(settings: Settings, query: str, endpoint: str) -> CloudModels:
    cloud = CloudModels(
        settings.model_copy(
            update={
                "api_response_log_path": settings.live_api_log_path,
                "api_log_content": False,
            }
        )
    )
    cloud.log_context = {
        "request_id": uuid.uuid4().hex,
        "endpoint": endpoint,
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
    }
    return cloud


def _error_detail(error: Exception, cloud: CloudModels) -> str:
    operation = getattr(error, "rag_operation", None) or cloud.last_operation
    stage = STAGES.get(operation, "retrieval")
    status = getattr(error, "status_code", None)
    provider = getattr(error, "rag_provider_error", {}) or {}
    if status:
        model = getattr(error, "rag_model", cloud.settings.chat_model)
        detail = (
            f"The model provider returned HTTP {status} during {stage} (model {model}). "
            f"{provider.get('message', '')}".rstrip()
        )
    elif isinstance(error, IndexNotReadyError):
        detail = str(error)
    else:
        detail = f"Failed during {stage}: {error!s}"
    detail = detail.rstrip()
    if not detail.endswith("."):
        detail += "."
    for key in (cloud.settings.openai_api_key, cloud.settings.embedding_api_key):
        if key:
            detail = detail.replace(key, "[REDACTED]")
    return f"{detail} Diagnostic ID: {cloud.log_context['request_id']}."


def _request_error(error: Exception, cloud: CloudModels) -> HTTPException:
    return HTTPException(status_code=503, detail=_error_detail(error, cloud))


def _citations(hits: list[dict]) -> list[Citation]:
    return [
        Citation(
            index=index,
            title=hit["title"],
            heading=hit["heading"],
            url=hit["url"],
            excerpt=hit["content"][:600].strip(),
            score=hit.get("score"),
            dense_rank=hit.get("dense_rank"),
            lexical_rank=hit.get("lexical_rank"),
            rerank_score=hit.get("rerank_score"),
        )
        for index, hit in enumerate(hits, 1)
    ]


def _history(request: ChatRequest) -> list[dict]:
    return [turn.model_dump() for turn in request.history]


class IndexNotReadyError(RuntimeError):
    """The requested chunking strategy has not been indexed yet."""


def _require_index(settings: Settings, strategy: str) -> None:
    if not Database(settings.sqlite_path).status()[strategy]["pages"]:
        raise IndexNotReadyError(
            f"The '{strategy}' index is empty. Build it with "
            f"`python -m app.cli index --sync --strategy {strategy}`."
        )


def _retrieve(settings: Settings, request: ChatRequest, cloud: CloudModels):
    """Resolve follow-ups, run non-strict hybrid retrieval and trim to the context budget."""
    _require_index(settings, request.strategy)
    history = _history(request)
    standalone = cloud.standalone_question(request.question, history) if history else None
    hits, trace = Retriever(settings).search_with_trace(
        standalone or request.question,
        strategy=request.strategy,
        rerank=request.rerank,
        limit=request.limit,
        models=cloud,
    )
    return standalone, cloud.context_hits(hits), trace


NO_HITS = "No relevant sections were found in the index. Check that the documentation is indexed."

app = FastAPI(
    title="Kubernetes Documentation Assistant",
    version=__version__,
    description="Hybrid RAG (dense + BM25 + RRF + LLM reranker) over the Kubernetes docs.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "chat_api_configured": bool(settings.openai_api_key),
        "chat_model": settings.chat_model,
        "rerank_model": settings.rerank_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.active_embedding_model(),
        "embedding_ready": settings.embedding_provider == "local"
        or bool(settings.embedding_api_key or settings.openai_api_key),
    }


@app.post("/api/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    settings = get_settings()
    cloud = _live_models(settings, request.query, "search")
    try:
        _require_index(settings, request.strategy)
        hits, trace = Retriever(settings).search_with_trace(
            request.query,
            strategy=request.strategy,
            rerank=request.rerank,
            limit=request.limit,
            models=cloud,
        )
    except Exception as error:
        raise _request_error(error, cloud) from error
    return SearchResponse(
        query=request.query,
        strategy=request.strategy,
        reranked=trace.rerank_applied,
        hits=[SearchHit.model_validate(hit) for hit in hits],
        trace=Trace.model_validate(trace.as_dict()),
    )


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    settings = get_settings()
    cloud = _live_models(settings, request.question, "chat")
    try:
        standalone, hits, trace = _retrieve(settings, request, cloud)
        answer = cloud.answer(request.question, hits, _history(request)) if hits else NO_HITS
    except Exception as error:
        raise _request_error(error, cloud) from error
    return ChatResponse(
        answer=answer,
        citations=_citations(hits),
        strategy=request.strategy,
        reranked=trace.rerank_applied,
        standalone_question=standalone,
        trace=Trace.model_validate(trace.as_dict()),
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Server-Sent Events: ``sources`` once retrieval finishes, then ``delta`` tokens,
    then ``done`` (or ``error``). Sources arrive before the first token."""
    settings = get_settings()
    cloud = _live_models(settings, request.question, "chat_stream")

    def events() -> Iterator[str]:
        started = time.perf_counter()
        try:
            yield _sse("status", {"stage": "retrieval"})
            standalone, hits, trace = _retrieve(settings, request, cloud)
            yield _sse(
                "sources",
                {
                    "citations": [item.model_dump() for item in _citations(hits)],
                    "standalone_question": standalone,
                    "reranked": trace.rerank_applied,
                    "trace": trace.as_dict(),
                },
            )
            if not hits:
                yield _sse("delta", {"text": NO_HITS})
            else:
                yield _sse("status", {"stage": "generation"})
                for text in cloud.answer_stream(request.question, hits, _history(request)):
                    yield _sse("delta", {"text": text})
            yield _sse(
                "done",
                {
                    "request_id": cloud.log_context["request_id"],
                    "total_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        except Exception as error:  # noqa: BLE001 - reported to the client as an SSE event
            yield _sse("error", {"detail": _error_detail(error, cloud)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/index/status")
def index_status() -> dict:
    settings = get_settings()
    return Database(settings.sqlite_path).status()


def _result_root() -> Path:
    return get_settings().eval_path.parent / "results"


def _latest_run() -> Path | None:
    root = _result_root()
    if not root.exists():
        return None
    runs = sorted(
        (path for path in root.iterdir() if path.is_dir() and (path / "summary.json").exists()),
        reverse=True,
    )
    return runs[0] if runs else None


@app.get("/api/eval/latest")
def latest_eval() -> dict:
    run = _latest_run()
    if run is None:
        return {"run_id": None, "metrics": []}
    audited = run / "audit" / "summary.json"
    return json.loads((audited if audited.exists() else run / "summary.json").read_text("utf-8"))


@lru_cache(maxsize=4)
def _question_matrix(run: Path, mtime: float) -> dict:
    del mtime  # cache key only: invalidates when the run is rewritten
    rows: dict[str, dict] = {}
    for _, _, label in LABELS:
        for record in read_records(run / f"{label}.jsonl"):
            row = rows.setdefault(
                record["id"],
                {
                    "id": record["id"],
                    "question": record["question"],
                    "relevant_urls": record["relevant_urls"],
                    "results": {},
                },
            )
            row["results"][label] = {
                "rank": record.get("rank"),
                "faithfulness": record.get("faithfulness"),
                "top_url": record["hits"][0]["url"] if record["hits"] else None,
            }
    return {"run_id": run.name, "questions": sorted(rows.values(), key=lambda row: row["id"])}


@app.get("/api/eval/latest/questions")
def latest_eval_questions() -> dict:
    """Per-question first-relevant rank and faithfulness for every configuration."""
    run = _latest_run()
    if run is None:
        return {"run_id": None, "questions": []}
    return _question_matrix(run, (run / "summary.json").stat().st_mtime)
