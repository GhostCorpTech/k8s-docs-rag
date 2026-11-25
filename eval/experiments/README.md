# Live experiments

Budget-capped follow-up checks run with `scripts/experiments/live_checks.py` against the
same model as the benchmark. The router endpoint is recorded as `openai-compatible-router`.

| Directory | What | Calls | Metered cost |
|---|---|---:|---:|
| [`http400-diagnostics/`](http400-diagnostics) | Every request shape the app sends, repeated, without retries | 43 (+1 unmetered `stream_options` probe) | $0.0055 |
| [`citation-prompt/`](citation-prompt) | Benchmark prompt (control) vs strict citation prompt on recorded contexts, judged for faithfulness | 201 | $0.0555 |

Write-up: [docs/EVALUATION.md §11](../../docs/EVALUATION.md#11-live-checks-after-release).
Each directory holds `summary.json` and the raw `api-responses.jsonl` (usage, cost, latency, completions);
`citation-prompt/results.jsonl` has every generated answer with its citation statistics and judge score.
