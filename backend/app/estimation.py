import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from .cloud import CloudModels
from .config import Settings
from .costs import lookup_rates, token_cost
from .token_budget import TokenBudget


def estimate_evaluation_cost(settings: Settings, historical_run: Path) -> dict:
    """Offline planning estimate, not a GLM tokenizer count or a hard spending cap."""
    tokenizer = TokenBudget.from_cache(
        settings.model_cache_path, settings.local_embedding_model
    ).tokenizer
    # Even a future accidental SDK call cannot use the live keys in this estimator.
    cloud = CloudModels(settings.model_copy(update={"openai_api_key": "", "embedding_api_key": ""}))
    inputs, outputs, calls = Counter(), Counter(), Counter()
    current_answer, current_candidates = "", 0

    def completion(operation: str, **kwargs):
        calls[operation] += 1
        inputs[operation] += sum(
            len(tokenizer.encode(message["content"]).ids) + 6 for message in kwargs["messages"]
        )
        if operation == "rerank":
            text = json.dumps(
                {"ranking": [{"id": index, "score": 0.9} for index in range(current_candidates)]}
            )
            # Historical JSONL saved only the final ten hits, not the 20 rerank candidates.
            inputs[operation] += sum(
                len(tokenizer.encode(message["content"]).ids) + 6 for message in kwargs["messages"]
            )
            outputs[operation] += len(tokenizer.encode(text).ids) * 2
        elif operation == "answer":
            text = current_answer
            outputs[operation] += len(tokenizer.encode(text).ids)
        elif operation == "faithfulness":
            text = '{"score":0.95,"reason":"Supported by the provided context"}'
            outputs[operation] += 64
        else:
            text = "English Kubernetes documentation search query"
            outputs[operation] += 64
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    cloud._complete = completion
    dataset = []
    for strategy in ("heading", "window"):
        for rerank in (False, True):
            file = historical_run / f"{strategy}_rerank_{'on' if rerank else 'off'}.jsonl"
            rows = [
                json.loads(line)
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if not rows or any(not row.get("generated_answer") for row in rows):
                raise ValueError("Cost estimate requires a complete historical faithfulness run")
            if not dataset:
                dataset = rows
            if len(rows) != len(dataset) or len({row["id"] for row in rows}) != len(dataset):
                raise ValueError("Historical evaluation configuration sizes differ")
            for row in rows:
                current_answer = row["generated_answer"]
                selected = cloud.context_hits(row["hits"][: settings.final_limit])
                if rerank:
                    current_candidates = len(row["hits"])
                    CloudModels.rerank.__wrapped__(cloud, row["question"], row["hits"])
                CloudModels.answer.__wrapped__(cloud, row["question"], selected)
                CloudModels.faithfulness.__wrapped__(
                    cloud, current_answer, [hit["content"] for hit in selected]
                )
    cached = set()
    if settings.sqlite_path.exists():
        with closing(
            sqlite3.connect(f"file:{settings.sqlite_path.as_posix()}?mode=ro", uri=True)
        ) as connection:
            cached = {row[0] for row in connection.execute("SELECT query FROM query_cache")}
    for row in dataset:
        if row["question"] not in cached:
            CloudModels.lexical_query.__wrapped__(cloud, row["question"])
    models = {
        "answer": settings.chat_model,
        "faithfulness": settings.judge_model,
        "rerank": settings.rerank_model,
        "translation": settings.chat_model,
    }
    operations = []
    for operation, count in calls.items():
        rates = lookup_rates(settings.api_pricing_path, settings.openai_base_url, models[operation])
        operations.append(
            {
                "operation": operation,
                "model": models[operation],
                "calls": count,
                "rates": rates,
                "estimated_prompt_tokens": math.ceil(inputs[operation] * 1.5),
                "estimated_visible_output_tokens": math.ceil(outputs[operation] * 1.5),
            }
        )
    scenarios = []
    for reasoning in (0, 2048, 4096):
        costs = [
            token_cost(
                {
                    "prompt_tokens": item["estimated_prompt_tokens"],
                    "completion_tokens": item["estimated_visible_output_tokens"]
                    + reasoning * item["calls"],
                },
                item["rates"],
            )
            for item in operations
        ]
        total = sum(costs) if all(cost is not None for cost in costs) else None
        scenarios.append(
            {
                "assumed_reasoning_tokens_per_call": reasoning,
                "estimated_usd": total,
                "planning_reserve_50_percent_usd": total * 1.5 if total is not None else None,
                "three_attempts_at_same_token_volume_usd": total * 3 if total is not None else None,
            }
        )
    return {
        "provider": settings.openai_base_url,
        "historical_run": historical_run.name,
        "questions_per_configuration": len(dataset),
        "configurations": 4,
        "cloud_calls_without_retries": sum(calls.values()),
        "local_embedding_api_cost_usd": 0,
        "operations": operations,
        "scenarios": scenarios,
        "estimated_prompt_tokens": sum(item["estimated_prompt_tokens"] for item in operations),
        "estimated_visible_output_tokens": sum(
            item["estimated_visible_output_tokens"] for item in operations
        ),
        "limitations": [
            "Offline estimate based on historical retrieved text/answers and current prompt templates. No cloud calls were made.",
            "Cached E5 tokenizer is only a proxy for the router model tokenizer. Token volumes are multiplied by 1.5 to allow for tokenizer and v4-context differences.",
            "Rerank input/output scaled from 10 stored hits to 20 candidates; actual candidates and future answers can differ.",
            "Reasoning tokens are unknown before requests. Scenarios are planning assumptions, NOT guaranteed upper bounds. No output-token cap is currently configured.",
            "No cache discount assumed; retries, tariff changes or longer reasoning can increase spending. Three-attempt scenario uses the same assumed volume, not a hard cap.",
            "Second-router cost is unknown until its model and actual tariff are supplied. Historical run's actual spend cannot be reconstructed without API usage or router billing records.",
            "Scope: RAG router API only, excluding electricity and this OpenCode conversation/provider billing.",
        ],
    }
