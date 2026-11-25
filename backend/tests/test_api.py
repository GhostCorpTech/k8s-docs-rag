import json
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.retrieval import RetrievalTrace

HIT = {
    "chunk_id": "1",
    "doc_id": "pods.md",
    "title": "Pods",
    "heading": "Pods > Lifecycle",
    "url": "https://kubernetes.io/docs/concepts/workloads/pods/",
    "content": "A Pod is the smallest deployable unit.",
    "score": 0.03,
    "dense_rank": 1,
    "lexical_rank": 2,
    "rerank_score": 0.9,
}


class FakeCloud:
    def __init__(self, fail_stream: bool = False):
        self.log_context = {"request_id": "req-1"}
        self.settings = Settings(_env_file=None)
        self.last_operation = None
        self.fail_stream = fail_stream
        self.calls: list[tuple] = []

    def standalone_question(self, question, history):
        self.calls.append(("condense", question, len(history)))
        return "How do I scale a Deployment?"

    def context_hits(self, hits):
        return hits

    def answer(self, question, hits, history=()):
        self.calls.append(("answer", question, len(history)))
        return "Use kubectl scale [1]."

    def answer_stream(self, question, hits, history=()):
        yield "Use kubectl "
        if self.fail_stream:
            self.last_operation = "answer"
            raise RuntimeError("stream broke")
        yield "scale [1]."


class FakeRetriever:
    queries: ClassVar[list[str]] = []

    def __init__(self, settings):
        pass

    def search_with_trace(self, query, **kwargs):
        FakeRetriever.queries.append(query)
        trace = RetrievalTrace(strategy=kwargs["strategy"], rerank_requested=kwargs["rerank"])
        trace.rerank_applied = kwargs["rerank"]
        return [dict(HIT)], trace


@pytest.fixture
def cloud(monkeypatch) -> FakeCloud:
    fake = FakeCloud()
    FakeRetriever.queries = []
    monkeypatch.setattr("app.main._require_index", lambda *args: None)
    monkeypatch.setattr("app.main._live_models", lambda *args: fake)
    monkeypatch.setattr("app.main.Retriever", FakeRetriever)
    return fake


def _events(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_chat_returns_citations_with_retrieval_scores(cloud: FakeCloud) -> None:
    response = TestClient(app).post("/api/chat", json={"question": "Что такое Pod?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Use kubectl scale [1]."
    assert body["citations"][0]["rerank_score"] == 0.9
    assert body["citations"][0]["dense_rank"] == 1
    assert body["standalone_question"] is None and body["reranked"] is False
    assert body["strategy"] == "window"
    assert ("condense", "Что такое Pod?", 0) not in cloud.calls


def test_follow_up_is_condensed_before_retrieval(cloud: FakeCloud) -> None:
    history = [
        {"role": "user", "content": "What is a Deployment?"},
        {"role": "assistant", "content": "A Deployment manages ReplicaSets [1]."},
    ]
    response = TestClient(app).post(
        "/api/chat", json={"question": "How do I scale it?", "history": history}
    )
    body = response.json()
    assert body["standalone_question"] == "How do I scale a Deployment?"
    assert FakeRetriever.queries == ["How do I scale a Deployment?"]
    assert ("answer", "How do I scale it?", 2) in cloud.calls


def test_stream_sends_sources_before_tokens(cloud: FakeCloud) -> None:
    response = TestClient(app).post("/api/chat/stream", json={"question": "Что такое Pod?"})
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    names = [name for name, _ in events]
    assert names == ["status", "sources", "status", "delta", "delta", "done"]
    assert events[1][1]["citations"][0]["url"] == HIT["url"]
    assert "".join(data["text"] for name, data in events if name == "delta") == (
        "Use kubectl scale [1]."
    )
    assert events[-1][1]["request_id"] == "req-1"


def test_stream_failure_is_reported_as_event(monkeypatch) -> None:
    fake = FakeCloud(fail_stream=True)
    monkeypatch.setattr("app.main._require_index", lambda *args: None)
    monkeypatch.setattr("app.main._live_models", lambda *args: fake)
    monkeypatch.setattr("app.main.Retriever", FakeRetriever)
    events = _events(
        TestClient(app).post("/api/chat/stream", json={"question": "Что такое Pod?"}).text
    )
    assert events[-1][0] == "error"
    assert "answer generation" in events[-1][1]["detail"]
    assert "req-1" in events[-1][1]["detail"]


def test_history_is_bounded() -> None:
    history = [{"role": "user", "content": "x"}] * 13
    response = TestClient(app).post("/api/chat", json={"question": "Question", "history": history})
    assert response.status_code == 422


def test_eval_question_matrix(tmp_path: Path, monkeypatch) -> None:
    eval_path = tmp_path / "eval" / "questions.jsonl"
    run = tmp_path / "eval" / "results" / "20260101T000000Z"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"run_id": run.name, "metrics": []}))
    for label in (
        "heading_rerank_off",
        "heading_rerank_on",
        "window_rerank_off",
        "window_rerank_on",
    ):
        record = {
            "id": "q001",
            "question": "Q",
            "relevant_urls": [HIT["url"]],
            "hits": [HIT],
            "rank": 1 if label.startswith("window") else None,
            "faithfulness": 1.0,
        }
        (run / f"{label}.jsonl").write_text(json.dumps(record) + "\n")
    settings = Settings(_env_file=None, eval_path=eval_path)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    client = TestClient(app)
    assert client.get("/api/eval/latest").json()["run_id"] == run.name
    matrix = client.get("/api/eval/latest/questions").json()
    results = matrix["questions"][0]["results"]
    assert (
        results["window_rerank_on"]["rank"] == 1 and results["heading_rerank_off"]["rank"] is None
    )


def test_empty_index_gives_actionable_error(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(_env_file=None, sqlite_path=tmp_path / "empty.db")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.main._live_models", lambda *args: FakeCloud())
    response = TestClient(app).post("/api/chat", json={"question": "Что такое Pod?"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "'window' index is empty" in detail and "app.cli index" in detail
    assert detail.endswith("Diagnostic ID: req-1.")
