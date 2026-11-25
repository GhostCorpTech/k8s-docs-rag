import hashlib
import json
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .cloud import CloudModels
from .config import get_settings
from .database import Database
from .models import (
    ChatRequest,
    ChatResponse,
    Citation,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from .retrieval import Retriever


def _live_models(settings, query: str, endpoint: str) -> CloudModels:
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


def _request_error(error: Exception, cloud: CloudModels) -> HTTPException:
    operation = getattr(error, "rag_operation", None) or cloud.last_operation
    stage = {
        "translation": "перевода запроса",
        "rerank": "reranker",
        "answer": "генерации ответа",
    }.get(operation, "поиска")
    status = getattr(error, "status_code", None)
    provider = getattr(error, "rag_provider_error", {})
    if status:
        detail = f"Роутер вернул HTTP {status} на этапе {stage} (модель {getattr(error, 'rag_model', cloud.settings.chat_model)}). {provider.get('message', '')}"
        if status == 400 and operation == "rerank":
            detail += " Попробуйте выключить Reranker; это обходной вариант, не подтверждение исправления роутера."
    else:
        detail = f"Ошибка на этапе {stage}: {error!s}"
    for key in (cloud.settings.openai_api_key, cloud.settings.embedding_api_key):
        if key:
            detail = detail.replace(key, "[REDACTED]")
    detail += f" ID диагностики: {cloud.log_context['request_id']}."
    return HTTPException(status_code=503, detail=detail)


app = FastAPI(
    title="Kubernetes Documentation Assistant",
    version="0.1.0",
    description="Hybrid RAG over the official Kubernetes documentation.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "chat_api_configured": bool(settings.openai_api_key),
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.active_embedding_model(),
        "embedding_ready": settings.embedding_provider == "local"
        or bool(settings.embedding_api_key or settings.openai_api_key),
        "database": str(settings.sqlite_path),
    }


@app.post("/api/search", response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
    settings = get_settings()
    cloud = _live_models(settings, request.query, "search")
    try:
        hits = Retriever(settings).search(
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
        reranked=request.rerank,
        hits=[SearchHit.model_validate(hit) for hit in hits],
    )


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    settings = get_settings()
    cloud = _live_models(settings, request.question, "chat")
    try:
        hits = Retriever(settings).search(
            request.question,
            strategy=request.strategy,
            rerank=request.rerank,
            limit=request.limit,
            models=cloud,
        )
        if not hits:
            return ChatResponse(
                answer="В индексе не найдено подходящих разделов. Проверьте, что документация проиндексирована.",
                citations=[],
                strategy=request.strategy,
                reranked=request.rerank,
            )
        hits = cloud.context_hits(hits)
        answer = cloud.answer(request.question, hits)
    except Exception as error:
        raise _request_error(error, cloud) from error

    citations = [
        Citation(
            index=index,
            title=hit["title"],
            heading=hit["heading"],
            url=hit["url"],
            excerpt=hit["content"][:360].strip(),
        )
        for index, hit in enumerate(hits, 1)
    ]
    return ChatResponse(
        answer=answer,
        citations=citations,
        strategy=request.strategy,
        reranked=request.rerank,
    )


@app.get("/api/index/status")
def index_status() -> dict:
    settings = get_settings()
    return Database(settings.sqlite_path).status()


@app.get("/api/eval/latest")
def latest_eval() -> dict:
    result_root = get_settings().eval_path.parent / "results"
    summaries = (
        [
            path / "audit" / "summary.json"
            if (path / "audit" / "summary.json").exists()
            else path / "summary.json"
            for path in sorted(result_root.iterdir(), reverse=True)
            if path.is_dir() and (path / "summary.json").exists()
        ]
        if result_root.exists()
        else []
    )
    if not summaries:
        return {"run_id": None, "metrics": []}
    return json.loads(summaries[0].read_text(encoding="utf-8"))
