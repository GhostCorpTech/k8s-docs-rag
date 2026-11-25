import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import BadRequestError

from app.cloud import CloudModels
from app.config import Settings
from app.main import _request_error, app


def test_provider_400_has_stage_and_redacts_key(tmp_path: Path) -> None:
    key = "private-key-for-test"
    cloud = CloudModels(
        Settings(_env_file=None, openai_api_key=key, api_response_log_path=tmp_path / "trace.jsonl")
    )
    cloud.log_context = {"request_id": "test-request"}
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://router.test/v1/chat/completions")
    )
    error = BadRequestError(
        "bad", response=response, body={"error": {"code": 400, "message": f"invalid request {key}"}}
    )

    def fail(**kwargs):
        raise error

    cloud.chat_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fail))
    )
    with pytest.raises(BadRequestError):
        cloud._complete(
            "translation",
            model="test-model",
            messages=[{"role": "user", "content": "Private question"}],
            temperature=0,
        )
    detail = _request_error(error, cloud).detail
    assert "HTTP 400" in detail and "перевода запроса" in detail
    assert "test-request" in detail
    text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert key not in detail and key not in text and "Private question" not in text
    assert json.loads(text)["http_status"] == 400


def test_live_success_log_keeps_usage_not_content(tmp_path: Path) -> None:
    cloud = CloudModels(
        Settings(
            _env_file=None,
            openai_api_key="",
            api_response_log_path=tmp_path / "trace.jsonl",
            api_log_content=False,
        )
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Private answer"))],
        usage={"prompt_tokens": 20, "completion_tokens": 5},
    )
    cloud.chat_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )
    cloud._complete(
        "answer", model="test-model", messages=[{"role": "user", "content": "Private question"}]
    )
    text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "Private answer" not in text and "Private question" not in text
    record = json.loads(text)
    assert record["usage"]["completion_tokens"] == 5
    assert record["choices"][0]["content"] is None


def test_chat_error_explains_reranker_and_returns_diagnostic_id(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(_env_file=None, live_api_log_path=tmp_path / "live.jsonl")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    class FailingRetriever:
        def __init__(self, settings):
            pass

        def search(self, *args, **kwargs):
            response = httpx.Response(400, request=httpx.Request("POST", "https://router.test"))
            error = BadRequestError("invalid request", response=response, body={})
            error.rag_operation = "rerank"
            error.rag_model = "test-model"
            error.rag_provider_error = {"message": "invalid request"}
            raise error

    monkeypatch.setattr("app.main.Retriever", FailingRetriever)
    response = TestClient(app).post("/api/chat", json={"question": "Question", "rerank": True})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "reranker" in detail and "выключить" in detail
    assert "ID диагностики" in detail
