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
