"""Build UI fixtures from a recorded evaluation run (real answers, sources and scores).

The README screenshots replay these recordings through the real frontend; nothing is
synthesised except fields the run did not record (timings are left empty on purpose).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import _question_matrix

RUN = ROOT / "eval/results/20261119T184220545709Z"
CONFIG = "window_rerank_off"
MAIN_QUESTION = "q012"
HISTORY = ["q005", "q019", "q031", "q044"]


def records() -> dict[str, dict]:
    lines = (RUN / f"{CONFIG}.jsonl").read_text("utf-8").splitlines()
    return {row["id"]: row for row in map(json.loads, lines)}


def citations(row: dict) -> list[dict]:
    return [
        {
            "index": index,
            "title": hit["title"],
            "heading": hit["heading"],
            "url": hit["url"],
            "excerpt": hit["content"][:600],
            "score": hit.get("score"),
            "dense_rank": hit.get("dense_rank"),
            "lexical_rank": hit.get("lexical_rank"),
            "rerank_score": hit.get("rerank_score"),
        }
        for index, hit in enumerate(row["answer_context"], 1)
    ]


def trace(row: dict) -> dict:
    return {
        "strategy": "window",
        "lexical_query": row["lexical_query"],
        "dense_candidates": 30,
        "lexical_candidates": 30,
        "fused_candidates": 0,
        "rerank_requested": False,
        "rerank_applied": False,
        "degraded": [],
        "timings_ms": {},
    }


def conversation(row: dict, offset: int) -> dict:
    return {
        "id": f"demo-{row['id']}",
        "title": row["question"],
        "createdAt": 1_763_500_000_000 - offset,
        "updatedAt": 1_763_500_000_000 - offset,
        "messages": [
            {"role": "user", "id": f"{row['id']}-u", "content": row["question"]},
            {
                "role": "assistant",
                "id": f"{row['id']}-a",
                "content": row["generated_answer"],
                "status": "done",
                "citations": citations(row),
                "settings": {"strategy": "window", "rerank": False, "limit": 8},
                "trace": trace(row),
            },
        ],
    }


def sse(row: dict) -> str:
    events = [("status", {"stage": "retrieval"})]
    events.append(
        (
            "sources",
            {
                "citations": citations(row),
                "standalone_question": None,
                "reranked": False,
                "trace": trace(row),
            },
        )
    )
    events.append(("status", {"stage": "generation"}))
    answer = row["generated_answer"]
    events += [
        ("delta", {"text": answer[i : i + 40]}) for i in range(0, len(answer), 40)
    ]
    events.append(("done", {"request_id": "recorded-eval-replay"}))
    return "".join(
        f"event: {e}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for e, d in events
    )


def main(out: Path) -> None:
    rows = records()
    summary = json.loads((RUN / "summary.json").read_text("utf-8"))
    manifest = summary["index"]["counts"]
    payload = {
        "health": {
            "status": "ok",
            "version": "1.0.0",
            "chat_api_configured": True,
            "chat_model": summary["configuration"]["chat_model"],
            "rerank_model": summary["configuration"]["rerank_model"],
            "embedding_model": summary["configuration"]["embedding_model"],
        },
        "index": {k: {**v, "last_indexed": None} for k, v in manifest.items()},
        "summary": summary,
        "questions": _question_matrix(RUN, 0.0),
        "question": rows[MAIN_QUESTION]["question"],
        "stream": sse(rows[MAIN_QUESTION]),
        "history": [
            conversation(rows[qid], i * 3_600_000) for i, qid in enumerate(HISTORY, 1)
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main(
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else ROOT / ".tmp/screenshot-fixtures.json"
    )
