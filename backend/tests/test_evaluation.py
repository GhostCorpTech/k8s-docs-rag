import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.evaluation import (
    EVALUATION_VERSION,
    _atomic_json,
    _configuration,
    _digest,
    _rank_of_relevant,
    _read_records,
    _recall,
    _run_lock,
    _validate_records,
    audit_evaluation,
    evaluate_configuration,
    load_questions,
    metrics_from_records,
    resume_evaluation,
    run_evaluation,
)


def test_rank_matches_page_while_ignoring_anchor() -> None:
    rank = _rank_of_relevant(
        [
            "https://kubernetes.io/docs/other/#x",
            "https://kubernetes.io/docs/concepts/workloads/pods/#pod-lifetime",
        ],
        ["https://kubernetes.io/docs/concepts/workloads/pods/"],
    )
    assert rank == 2


def test_rank_none_when_page_is_absent() -> None:
    assert _rank_of_relevant(["https://kubernetes.io/docs/other/"], ["https://x/"]) is None


def test_recall_is_fraction_of_relevant_pages_not_hit_rate() -> None:
    relevant = ["https://kubernetes.io/docs/a/", "https://kubernetes.io/docs/b/"]
    urls = [relevant[0] + "#one", relevant[0] + "#two"]
    assert _recall(urls, relevant, 5) == 0.5
    metric = metrics_from_records(
        [{"hits": [{"url": url} for url in urls], "relevant_urls": relevant}], "heading", False
    )
    assert metric.recall_at_5 == 0.5
    assert metric.hit_rate_at_5 == 1
    assert metric.mrr_at_10 == 1


def question() -> dict:
    return {
        "id": "q1",
        "question": "Question?",
        "reference_answer": "Reference",
        "relevant_urls": ["https://kubernetes.io/docs/a/"],
    }


def test_completed_rows_do_not_create_clients(tmp_path: Path, monkeypatch) -> None:
    item = question()
    path = tmp_path / "heading.jsonl"
    path.write_text(
        json.dumps(
            {
                **item,
                "hits": [{"url": item["relevant_urls"][0]}],
                "generated_answer": "Answer",
                "faithfulness": 0.8,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def forbidden(*args):
        raise AssertionError("No clients/API calls for completed rows")

    monkeypatch.setattr("app.evaluation.Retriever", forbidden)
    monkeypatch.setattr("app.evaluation.CloudModels", forbidden)
    metric = evaluate_configuration(Settings(_env_file=None), [item], "heading", False, True, path)
    assert metric.questions == 1
    assert metric.faithfulness == 0.8


def test_checkpoint_preserves_answer_after_judge_failure(tmp_path: Path, monkeypatch) -> None:
    item = question()
    counts = {"search": 0, "answer": 0, "judge": 0}
    hits = [{"url": item["relevant_urls"][0], "content": str(i)} for i in range(10)]

    class FakeRetriever:
        database = SimpleNamespace(cached_lexical_query=lambda query: query)

        def __init__(self, settings):
            pass

        def search(self, *args, **kwargs):
            counts["search"] += 1
            return hits

    class FakeCloud:
        def __init__(self, settings):
            pass

        def context_hits(self, selected):
            return selected

        def answer(self, query, selected):
            counts["answer"] += 1
            assert len(selected) == 8
            return "Answer"

        def faithfulness(self, answer, contexts):
            counts["judge"] += 1
            assert contexts == [str(i) for i in range(8)]
            if counts["judge"] == 1:
                raise RuntimeError("balance")
            return 0.9

    monkeypatch.setattr("app.evaluation.Retriever", FakeRetriever)
    monkeypatch.setattr("app.evaluation.CloudModels", FakeCloud)
    settings = Settings(_env_file=None)
    path = tmp_path / "heading.jsonl"
    with pytest.raises(RuntimeError, match="balance"):
        evaluate_configuration(settings, [item], "heading", False, True, path)
    evaluate_configuration(settings, [item], "heading", False, True, path)
    assert counts == {"search": 1, "answer": 1, "judge": 2}
    saved = _read_records(path)
    assert len(saved) == 1 and len(saved[0]["answer_context"]) == 8


def test_resume_rejects_changed_provider_before_api(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text(json.dumps(question()) + "\n", encoding="utf-8")
    settings = Settings(_env_file=None, eval_path=path)
    run = tmp_path / "run"
    run.mkdir()
    manifest = {
        "evaluation_version": EVALUATION_VERSION,
        "configuration": _configuration(settings),
        "questions_sha256": _digest([question()]),
    }
    _atomic_json(run / "manifest.json", manifest)
    settings.chat_model = "different-provider-model"
    with pytest.raises(ValueError, match="Cannot resume"):
        resume_evaluation(settings, run)


def test_partial_tail_is_preserved_before_repair(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    original = b'{"id":"q1"}\n{"id":"q'
    path.write_bytes(original)
    assert _read_records(path, repair_tail=True) == [{"id": "q1"}]
    assert path.read_bytes() == b'{"id":"q1"}\n'
    assert next(tmp_path.glob("*.interrupted-*")).read_bytes() == original


def test_invalid_middle_record_is_not_discarded(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    original = b'{"id":"q1"}\nnot-json\n{"id":"q2"}\n'
    path.write_bytes(original)
    with pytest.raises(ValueError, match="line 2"):
        _read_records(path, repair_tail=True)
    assert path.read_bytes() == original


def test_dataset_and_saved_records_reject_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text((json.dumps(question()) + "\n") * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_questions(path)
    record = {**question(), "hits": []}
    with pytest.raises(ValueError, match="duplicate"):
        _validate_records([record, record], [question()], False)


def test_run_lock_prevents_double_append(tmp_path: Path) -> None:
    with (
        _run_lock(tmp_path),
        pytest.raises(RuntimeError, match="already running"),
        _run_lock(tmp_path),
    ):
        pass
    with _run_lock(tmp_path):
        pass


def test_new_manifest_exists_before_first_evaluation_call(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text(json.dumps(question()) + "\n", encoding="utf-8")
    settings = Settings(_env_file=None, eval_path=path)
    monkeypatch.setattr(
        "app.evaluation._index_identity", lambda settings: {"source_commit": "snapshot"}
    )

    def interrupt(settings, questions, strategy, reranker, faithfulness, output, workers=1):
        manifest = json.loads((output.parent / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["configuration"]["chat_model"] == settings.chat_model
        assert not manifest["with_faithfulness"]
        assert (output.parent / "questions.jsonl").exists()
        raise RuntimeError("first API failure")

    monkeypatch.setattr("app.evaluation.evaluate_configuration", interrupt)
    with pytest.raises(RuntimeError, match="first API failure"):
        run_evaluation(settings)
    assert len(list((tmp_path / "results").glob("*/manifest.json"))) == 1


def test_resume_preserves_retrieval_only_mode(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "questions.jsonl"
    path.write_text(json.dumps(question()) + "\n", encoding="utf-8")
    settings = Settings(_env_file=None, eval_path=path)
    run = tmp_path / "run"
    run.mkdir()
    manifest = {
        "evaluation_version": EVALUATION_VERSION,
        "configuration": _configuration(settings),
        "questions_sha256": _digest([question()]),
        "with_faithfulness": False,
        "index": {"source_commit": "snapshot"},
    }
    _atomic_json(run / "manifest.json", manifest)
    monkeypatch.setattr("app.evaluation._index_identity", lambda settings: manifest["index"])

    def resumed(settings, questions, saved_manifest, result_dir, workers=1):
        assert saved_manifest["with_faithfulness"] is False
        return result_dir

    monkeypatch.setattr("app.evaluation._run_configurations", resumed)
    assert resume_evaluation(settings, run) == run


def test_audit_preserves_originals_and_marks_legacy_faithfulness(tmp_path: Path) -> None:
    item = question()
    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps(item) + "\n", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    originals = {}
    for strategy in ("heading", "window"):
        for rerank in ("off", "on"):
            file = run / f"{strategy}_rerank_{rerank}.jsonl"
            file.write_text(
                json.dumps(
                    {
                        **item,
                        "hits": [{"url": item["relevant_urls"][0], "rerank_score": 0}],
                        "generated_answer": "Answer",
                        "faithfulness": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            originals[file] = file.read_bytes()
    summary = run / "summary.json"
    summary.write_text(
        json.dumps({"run_id": "legacy", "with_faithfulness": True}), encoding="utf-8"
    )
    originals[summary] = summary.read_bytes()
    audit = audit_evaluation(Settings(_env_file=None, eval_path=questions), run)
    payload = json.loads((audit / "summary.json").read_text(encoding="utf-8"))
    assert payload["faithfulness_verified"] is False
    assert payload["metrics"][0]["recall_at_5"] == 1
    assert payload["warnings"] and payload["suspicious_records"]
    assert all(path.read_bytes() == content for path, content in originals.items())
