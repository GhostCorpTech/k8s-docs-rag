import json
from pathlib import Path

import pytest

from app.costs import summarize_costs, token_cost

RATES = {"input_per_million": 0.039, "output_per_million": 0.14, "cached_input_per_million": 0.011}


def test_cached_tokens_are_discounted_and_reasoning_is_not_double_counted() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "prompt_tokens_details": {"cached_tokens": 600},
        "completion_tokens_details": {"reasoning_tokens": 300},
    }
    assert token_cost(usage, RATES) == pytest.approx(
        (400 * 0.039 + 600 * 0.011 + 500 * 0.14) / 1_000_000
    )


@pytest.mark.parametrize(
    "usage,rates",
    [
        (None, RATES),
        ({}, RATES),
        ({"prompt_tokens": 1, "completion_tokens": 2}, None),
        ({"prompt_tokens": -1, "completion_tokens": 2}, RATES),
    ],
)
def test_unknown_or_invalid_usage_is_not_zero(usage, rates) -> None:
    assert token_cost(usage, rates) is None


def test_cost_summary_includes_retries_and_unknown_failures(tmp_path: Path) -> None:
    path = tmp_path / "api.jsonl"
    rows = [
        {
            "operation": "rerank",
            "token_cost_usd": 0.01,
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        },
        {
            "operation": "rerank",
            "token_cost_usd": 0.02,
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        },
        {"operation": "answer", "token_cost_usd": None, "usage": None},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    summary = summarize_costs(path)
    assert summary["known_token_cost_usd"] == pytest.approx(0.03)
    assert summary["operations"]["rerank"] == 2
    assert summary["completion_tokens"] == 60
    assert summary["unknown_cost_attempts"] == 1
    assert summary["fully_metered"] is False


def test_historical_cost_without_logs_is_unknown(tmp_path: Path) -> None:
    summary = summarize_costs(tmp_path / "missing.jsonl")
    assert summary["known_token_cost_usd"] is None
    assert summary["fully_metered"] is False


def test_missing_rate_snapshot_can_be_reconciled_without_changing_logs(tmp_path: Path) -> None:
    log = tmp_path / "api.jsonl"
    record = {
        "operation": "answer",
        "provider": "https://router.test/v1",
        "model": "test",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
        "pricing": None,
        "token_cost_usd": None,
    }
    original = json.dumps(record) + "\n"
    log.write_text(original, encoding="utf-8")
    pricing = tmp_path / "pricing.json"
    pricing.write_text(
        json.dumps({"providers": {"https://router.test/v1": {"test": RATES}}}), encoding="utf-8"
    )
    summary = summarize_costs(log, pricing)
    assert summary["fully_metered"] is True
    assert summary["reconciled_at_configured_tariff_attempts"] == 1
    assert summary["known_token_cost_usd"] == pytest.approx((1000 * 0.039 + 100 * 0.14) / 1_000_000)
    assert log.read_text(encoding="utf-8") == original


def test_wildcard_provider_prices_apply_to_any_endpoint(tmp_path) -> None:
    import json

    from app.costs import lookup_rates

    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "*": {"m": {"input_per_million": 1, "output_per_million": 2}},
                    "https://exact.test/v1": {
                        "m": {"input_per_million": 5, "output_per_million": 6}
                    },
                }
            }
        )
    )
    assert lookup_rates(path, "https://any.test/v1", "m")["input_per_million"] == 1
    assert lookup_rates(path, "https://exact.test/v1/", "m")["input_per_million"] == 5
    assert lookup_rates(path, "https://any.test/v1", "unknown") is None
