import json
from collections import Counter
from pathlib import Path


def lookup_rates(path: Path, provider: str, model: str) -> dict | None:
    if not path.exists():
        return None
    providers = json.loads(path.read_text(encoding="utf-8")).get("providers", {})
    # An exact provider entry wins; "*" holds model prices that apply to any endpoint.
    exact = providers.get(provider.rstrip("/"), {}).get(model)
    return exact if exact is not None else providers.get("*", {}).get(model)


def token_cost(usage: dict | None, rates: dict | None) -> float | None:
    """Reasoning tokens are already included in completion_tokens, not added twice."""
    if not usage or rates is None:
        return None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    if (
        any(type(value) is not int or value < 0 for value in (prompt, completion, cached))
        or cached > prompt
    ):
        return None
    return (
        (prompt - cached) * rates["input_per_million"]
        + cached * rates.get("cached_input_per_million", rates["input_per_million"])
        + completion * rates["output_per_million"]
    ) / 1_000_000


def summarize_costs(path: Path, pricing_path: Path | None = None) -> dict:
    records = (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if path.exists()
        else []
    )
    totals = Counter()
    costs, unknown, reconciled = [], 0, 0
    operations = Counter()
    for record in records:
        operations[record["operation"]] += 1
        usage = record.get("usage") or {}
        for field in ("prompt_tokens", "completion_tokens"):
            if type(usage.get(field)) is int:
                totals[field] += usage[field]
        cost = record.get("token_cost_usd")
        if cost is None and usage and pricing_path is not None:
            rates = record.get("pricing") or lookup_rates(
                pricing_path, record.get("provider", ""), record.get("model", "")
            )
            cost = token_cost(usage, rates)
            if cost is not None:
                reconciled += 1
        if cost is None:
            unknown += 1
        else:
            costs.append(cost)
    return {
        "request_attempts": len(records),
        "operations": dict(operations),
        "prompt_tokens": totals["prompt_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "known_token_cost_usd": sum(costs) if costs else None,
        "unknown_cost_attempts": unknown,
        "reconciled_at_configured_tariff_attempts": reconciled,
        "fully_metered": bool(records) and unknown == 0,
        "note": "Computed from API usage and the per-request pricing snapshot, not an invoice. Reconciled attempts use the supplied configured tariff when the request-time snapshot was absent; original API logs are not modified. Failed/time-out requests can have unknown charges; verify balance delta in the router panel. Includes retries and reasoning counted by the provider. Unknown costs are not treated as zero.",
    }
