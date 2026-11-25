"""Probe the LLM endpoint's health over time with tiny, always-fresh requests.

Every interval, sends N translation-sized requests whose text is unique (so no router-side
response cache can answer them) and appends the outcome to a JSONL log. Prints one line per
round. Used to show that the intermittent HTTP 400s depend on time, not on our requests.

  python scripts/experiments/provider_probe.py --rounds 20 --interval 180 --per-round 2
"""

import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "eval/experiments/http400-diagnostics"
TOPICS = [
    "Pod",
    "Service",
    "Ingress",
    "ConfigMap",
    "Secret",
    "Deployment",
    "Job",
    "PVC",
    "Node",
    "RBAC",
]
SYSTEM = (
    "Translate the question into a concise English Kubernetes documentation search query. "
    "Preserve API names, commands and flags. Return only the query."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--interval", type=float, default=180)
    parser.add_argument("--per-round", type=int, default=2)
    parser.add_argument("--model", default="glm-5.3-flash")
    parser.add_argument(
        "--label", default="a", help="endpoint label; the URL is never stored"
    )
    args = parser.parse_args()
    out = OUT_DIR / f"provider-probe-{args.label}.jsonl"
    client = OpenAI(
        max_retries=0, timeout=60
    )  # OPENAI_API_KEY / OPENAI_BASE_URL from env
    for round_index in range(args.rounds):
        ok = 0
        for i in range(args.per_round):
            topic = TOPICS[(round_index * args.per_round + i) % len(TOPICS)]
            question = f"Как проверить состояние объекта {topic} (проверка {uuid.uuid4().hex[:6]})?"
            started = time.perf_counter()
            record = {
                "at": datetime.now(UTC).isoformat(),
                "endpoint": args.label,
                "model": args.model,
            }
            try:
                client.chat.completions.create(
                    model=args.model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": question},
                    ],
                )
                record.update(ok=True, http_status=200)
                ok += 1
            except Exception as error:  # noqa: BLE001 - the failure is the measurement
                record.update(
                    ok=False,
                    http_status=getattr(error, "status_code", None),
                    error_type=type(error).__name__,
                )
            record["seconds"] = round(time.perf_counter() - started, 2)
            with out.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record) + "\n")
        print(f"{datetime.now(UTC):%H:%M} ok {ok}/{args.per_round}", flush=True)
        if round_index + 1 < args.rounds:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
