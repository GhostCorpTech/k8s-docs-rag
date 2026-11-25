import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from app.cloud import CloudModels, CloudOutputError, _json_from_text, _retryable, _score
from app.config import Settings


def cloud_with_output(text: str) -> CloudModels:
    cloud = CloudModels(Settings(_env_file=None, openai_api_key=""))
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    cloud.chat_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )
    return cloud


@pytest.mark.parametrize("text", ["", "not json", "[]"])
def test_invalid_json_is_not_a_zero_score(text: str) -> None:
    with pytest.raises(CloudOutputError):
        _json_from_text(text)


@pytest.mark.parametrize("score", [None, True, "0.8", float("nan"), float("inf"), -0.1, 1.1])
def test_score_validation(score: object) -> None:
    with pytest.raises(CloudOutputError):
        _score(score)


def test_valid_zero_faithfulness_is_preserved() -> None:
    assert (
        CloudModels.faithfulness.__wrapped__(
            cloud_with_output('{"score":0}'), "answer", ["context"]
        )
        == 0
    )


@pytest.mark.parametrize(
    "ranking",
    [
        "[]",
        '[{"id":0,"score":0.8},{"id":0,"score":0.9}]',
        '[{"id":0,"score":0.8},{"id":3,"score":0.9}]',
    ],
)
def test_invalid_ranking_does_not_silently_disable_reranker(ranking: str) -> None:
    hits = [{"title": "T", "heading": "H", "content": "C"}] * 2
    with pytest.raises(CloudOutputError):
        CloudModels.rerank.__wrapped__(cloud_with_output('{"ranking":' + ranking + "}"), "Q", hits)


def test_auth_and_balance_errors_are_not_retried() -> None:
    for status in (401, 402, 403, 404):
        response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
        assert not _retryable(APIStatusError("failed", response=response, body={}))


def test_only_context_that_fits_is_exposed() -> None:
    cloud = cloud_with_output("answer")
    cloud.settings.max_context_chars = 1000
    hits = [
        {"title": "T", "heading": "H", "url": "https://kubernetes.io/docs/", "content": "x" * 700}
    ] * 3
    assert len(cloud.context_hits(hits)) == 1


def test_invalid_raw_provider_output_is_retained_without_key(tmp_path: Path) -> None:
    cloud = cloud_with_output("not json")
    cloud.settings.openai_api_key = "secret-test-key"
    cloud.settings.api_response_log_path = tmp_path / "api-responses.jsonl"
    with pytest.raises(CloudOutputError):
        CloudModels.faithfulness.__wrapped__(cloud, "answer", ["context"])
    text = cloud.settings.api_response_log_path.read_text(encoding="utf-8")
    assert "secret-test-key" not in text
    assert json.loads(text)["choices"][0]["content"] == "not json"


def test_api_usage_and_pricing_snapshot_are_logged(tmp_path: Path) -> None:
    cloud = cloud_with_output('{"score":0.9}')
    cloud.settings.api_response_log_path = tmp_path / "api-responses.jsonl"
    cloud.settings.api_pricing_path = tmp_path / "pricing.json"
    cloud.settings.openai_base_url = "https://router.test/v1"
    cloud.settings.judge_model = "test-model"
    rates = {"input_per_million": 1, "output_per_million": 2}
    cloud.settings.api_pricing_path.write_text(
        json.dumps({"providers": {"https://router.test/v1": {"test-model": rates}}}),
        encoding="utf-8",
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"score":0.9}'))],
        usage={"prompt_tokens": 1000, "completion_tokens": 100},
    )
    cloud.chat_client.chat.completions.create = lambda **kwargs: response
    CloudModels.faithfulness.__wrapped__(cloud, "answer", ["context"])
    record = json.loads(cloud.settings.api_response_log_path.read_text(encoding="utf-8"))
    assert record["token_cost_usd"] == pytest.approx(0.0012)
    assert record["pricing"] == rates
    assert record["usage"]["completion_tokens"] == 100


def test_failed_request_has_explicitly_unknown_charge(tmp_path: Path) -> None:
    cloud = cloud_with_output("unused")
    cloud.settings.api_response_log_path = tmp_path / "api-responses.jsonl"

    def failed(**kwargs):
        raise RuntimeError("connection interrupted")

    cloud.chat_client.chat.completions.create = failed
    with pytest.raises(RuntimeError, match="connection interrupted"):
        CloudModels.faithfulness.__wrapped__(cloud, "answer", ["context"])
    record = json.loads(cloud.settings.api_response_log_path.read_text(encoding="utf-8"))
    assert record["status"] == "error"
    assert record["token_cost_usd"] is None


def _chunk(content=None, finish=None, usage=None, reasoning=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage
    )


def test_stream_forwards_content_but_not_reasoning_and_logs_usage(tmp_path: Path) -> None:
    cloud = cloud_with_output("")
    cloud.settings.api_response_log_path = tmp_path / "trace.jsonl"
    chunks = [
        _chunk(reasoning="thinking..."),
        _chunk(content="Pod "),
        _chunk(content="is [1]."),
        _chunk(finish="stop"),
        SimpleNamespace(choices=[], usage={"prompt_tokens": 10, "completion_tokens": 3}),
    ]
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return iter(chunks)

    cloud.chat_client.chat.completions.create = create
    hits = [{"title": "T", "heading": "H", "url": "https://kubernetes.io/docs/", "content": "c"}]
    assert "".join(cloud.answer_stream("Что такое Pod?", hits)) == "Pod is [1]."
    assert seen["stream"] is True and "stream_options" not in seen
    record = json.loads(cloud.settings.api_response_log_path.read_text(encoding="utf-8"))
    assert record["usage"]["completion_tokens"] == 3
    assert record["choices"][0] == {
        "content": "Pod is [1].",
        "content_chars": 11,
        "finish_reason": "stop",
    }
    assert record["first_token_seconds"] >= 0


def test_truncated_stream_is_an_error() -> None:
    cloud = cloud_with_output("")
    cloud.chat_client.chat.completions.create = lambda **kwargs: iter(
        [_chunk(content="partial"), _chunk(finish="length")]
    )
    hits = [{"title": "T", "heading": "H", "url": "https://kubernetes.io/docs/", "content": "c"}]
    with pytest.raises(CloudOutputError):
        list(cloud.answer_stream("Q", hits))


def test_history_is_passed_to_answer_and_capped() -> None:
    cloud = cloud_with_output("answer")
    history = [{"role": "user", "content": f"turn {i}"} for i in range(10)]
    hits = [{"title": "T", "heading": "H", "url": "https://kubernetes.io/docs/", "content": "c"}]
    messages = cloud._answer_messages("Q", hits, history)
    assert messages[0]["role"] == "system" and "языке вопроса" in messages[0]["content"]
    assert [m["content"] for m in messages[1:-1]] == [f"turn {i}" for i in range(4, 10)]
    assert messages[-1]["content"].startswith("Вопрос: Q")


def test_standalone_question_without_history_makes_no_call() -> None:
    cloud = cloud_with_output("unused")
    cloud.chat_client = None
    assert CloudModels.standalone_question.__wrapped__(cloud, "Q", []) == "Q"
