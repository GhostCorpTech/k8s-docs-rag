import json
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.evaluation import evaluate_configuration


def test_parallel_questions_are_bounded_and_written_once(tmp_path: Path, monkeypatch) -> None:
    questions = [
        {"id": f"q{i}", "question": f"Q{i}", "relevant_urls": ["https://kubernetes.io/docs/a/"]}
        for i in range(4)
    ]
    lock, barrier = Lock(), Barrier(2)
    counts = {"active": 0, "maximum": 0, "answers": 0, "judges": 0}

    class FakeRetriever:
        database = SimpleNamespace(cached_lexical_query=lambda query: query)

        def __init__(self, settings):
            pass

        def search(self, *args, **kwargs):
            return [{"url": "https://kubernetes.io/docs/a/", "content": "Evidence"}]

    class FakeCloud:
        def __init__(self, settings):
            pass

        def context_hits(self, hits):
            return hits

        def answer(self, question, hits):
            with lock:
                counts["active"] += 1
                counts["answers"] += 1
                counts["maximum"] = max(counts["maximum"], counts["active"])
            barrier.wait(timeout=5)
            with lock:
                counts["active"] -= 1
            return "Answer"

        def faithfulness(self, answer, contexts):
            with lock:
                counts["judges"] += 1
            return 0.9

    monkeypatch.setattr("app.evaluation.Retriever", FakeRetriever)
    monkeypatch.setattr("app.evaluation.CloudModels", FakeCloud)
    settings = Settings(_env_file=None, embedding_provider="openai")
    path = tmp_path / "heading.jsonl"
    metrics = evaluate_configuration(settings, questions, "heading", False, True, path, workers=2)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert counts == {"active": 0, "maximum": 2, "answers": 4, "judges": 4}
    assert metrics.questions == 4 and metrics.faithfulness_questions == 4
    assert len(rows) == len({row["id"] for row in rows}) == 4
    evaluate_configuration(settings, questions, "heading", False, True, path, workers=2)
    assert counts["answers"] == 4


def test_parallel_resume_skips_already_checkpointed_api_stages(tmp_path: Path, monkeypatch) -> None:
    from hashlib import sha256

    from app.evaluation import _atomic_json

    item = {"id": "q1", "question": "Question", "relevant_urls": ["https://kubernetes.io/docs/a/"]}
    record = {
        **item,
        "hits": [{"url": item["relevant_urls"][0], "content": "Evidence"}],
        "rank": 1,
        "answer_context": [{"content": "Evidence"}],
        "generated_answer": "Answer",
        "faithfulness": 0.8,
    }
    path = tmp_path / "heading.jsonl"
    checkpoint = (
        tmp_path / "checkpoints/heading" / (sha256(item["id"].encode()).hexdigest() + ".json")
    )
    _atomic_json(checkpoint, record)
    monkeypatch.setattr("app.evaluation.get_local_model", lambda *args: None)

    class NoApiCloud:
        def __init__(self, settings):
            pass

        def __getattr__(self, name):
            raise AssertionError(f"No API calls allowed: {name}")

    monkeypatch.setattr("app.evaluation.CloudModels", NoApiCloud)
    metrics = evaluate_configuration(
        Settings(_env_file=None), [item], "heading", False, True, path, workers=2
    )
    assert metrics.faithfulness == 0.8
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_worker_failure_cancels_backlog_and_preserves_inflight_answer(
    tmp_path: Path, monkeypatch
) -> None:
    from hashlib import sha256

    questions = [
        {"id": f"q{i}", "question": f"Q{i}", "relevant_urls": ["https://kubernetes.io/docs/a/"]}
        for i in range(3)
    ]
    barrier = Barrier(2)
    answers = []

    class FakeRetriever:
        database = SimpleNamespace(cached_lexical_query=lambda query: query)

        def __init__(self, settings):
            pass

        def search(self, *args, **kwargs):
            return [{"url": "https://kubernetes.io/docs/a/", "content": "Evidence"}]

    class FakeCloud:
        def __init__(self, settings):
            pass

        def context_hits(self, hits):
            return hits

        def answer(self, question, hits):
            answers.append(question)
            barrier.wait(timeout=5)
            if question == "Q0":
                raise RuntimeError("primary failure")
            assert self.stop_event.wait(timeout=5)
            return "Completed answer"

        def faithfulness(self, *args):
            raise AssertionError("No new judge request after stop")

    monkeypatch.setattr("app.evaluation.Retriever", FakeRetriever)
    monkeypatch.setattr("app.evaluation.CloudModels", FakeCloud)
    path = tmp_path / "heading.jsonl"
    with pytest.raises(RuntimeError):
        evaluate_configuration(
            Settings(_env_file=None, embedding_provider="openai"),
            questions,
            "heading",
            False,
            True,
            path,
            workers=2,
        )
    assert set(answers) == {"Q0", "Q1"}
    checkpoint = tmp_path / "checkpoints/heading" / (sha256(b"q1").hexdigest() + ".json")
    record = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert record["generated_answer"] == "Completed answer"
    assert "faithfulness" not in record
