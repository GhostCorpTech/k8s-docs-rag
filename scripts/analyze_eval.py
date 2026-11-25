"""Offline statistical analysis of a saved evaluation run. Makes no API calls.

Reads eval/results/<run>/*.jsonl + api-responses.jsonl and writes:
  docs/results/analysis.json   bootstrap CIs, paired tests, latency, cost, rank buckets
  docs/results/per_question.csv  first-relevant rank / faithfulness per question x config
  docs/results/metrics.csv     headline table
  docs/assets/charts/*-{light,dark}.svg

Usage: python scripts/analyze_eval.py [run_id]
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urldefrag

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = "20261119T184220545709Z"
LEGACY_RUN = "20261114T152921Z"
LABELS = [
    "heading_rerank_off",
    "heading_rerank_on",
    "window_rerank_off",
    "window_rerank_on",
]
PRETTY = {
    "heading_rerank_off": "Heading · no rerank",
    "heading_rerank_on": "Heading · LLM rerank",
    "window_rerank_off": "Window · no rerank",
    "window_rerank_on": "Window · LLM rerank",
}
SEED, RESAMPLES = 20261119, 10_000

# Validated with the dataviz palette validator (adjacent CVD dE 26.6 light / 27.5 dark).
THEMES = {
    "light": {
        "text": "#191919",
        "muted": "#666666",
        "grid": "#e4e4e4",
        "heading": "#257de0",
        "window": "#f47230",
        "neutral": "#c9c9c9",
        "good": "#257de0",
        "bad": "#ec4647",
        "ramp": ["#0f4a96", "#2370cd", "#519ced", "#9dc8f7"],
    },
    "dark": {
        "text": "#efefef",
        "muted": "#9c9c9c",
        "grid": "#30363d",
        "heading": "#338ced",
        "window": "#e76624",
        "neutral": "#4a4f57",
        "good": "#338ced",
        "bad": "#eb6263",
        "ramp": ["#cce3fd", "#84b9f3", "#338ced", "#1757ac"],
    },
}


# ----------------------------------------------------------------------------- data


def page(url: str) -> str:
    return urldefrag(url.strip())[0].rstrip("/") + "/"


def load_run(run: Path) -> dict[str, list[dict]]:
    data = {}
    for label in LABELS:
        rows = [
            json.loads(line)
            for line in (run / f"{label}.jsonl").read_text("utf-8").splitlines()
        ]
        data[label] = sorted(rows, key=lambda row: row["id"])
    ids = [[row["id"] for row in rows] for rows in data.values()]
    assert all(item == ids[0] for item in ids), (
        "configurations must cover identical questions"
    )
    return data


CITATION = re.compile(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]")


def citations(row: dict) -> tuple[int, int]:
    """(valid, invalid) [n] markers; invalid = pointing past the context given to the generator."""
    markers = [
        int(n)
        for group in CITATION.findall(row.get("generated_answer") or "")
        for n in group.split(",")
    ]
    limit = len(row.get("answer_context") or [])
    return sum(1 <= n <= limit for n in markers), sum(
        not 1 <= n <= limit for n in markers
    )


def per_question(row: dict) -> dict:
    valid, invalid = citations(row)
    urls = [hit["url"] for hit in row["hits"]]
    gold = {page(url) for url in row["relevant_urls"]}
    rank = next((i for i, url in enumerate(urls, 1) if page(url) in gold), None)
    recall = lambda k: len({page(url) for url in urls[:k]} & gold) / len(gold)
    return {
        "rank": rank,
        "rr": 1 / rank if rank and rank <= 10 else 0.0,
        "recall5": recall(5),
        "recall10": recall(10),
        "hit5": float(rank is not None and rank <= 5),
        "faith": row.get("faithfulness"),
        "cited": float(valid > 0),
        "invalid_citations": invalid,
        "answer_chars": len(row.get("generated_answer") or ""),
    }


# ------------------------------------------------------------------------ statistics


def bootstrap_ci(values: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    idx = rng.integers(0, len(values), size=(RESAMPLES, len(values)))
    means = values[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def paired(a: np.ndarray, b: np.ndarray, rng: np.random.Generator) -> dict:
    """b - a. Bootstrap CI of the mean difference and a two-sided sign-flip permutation test."""
    diff = b - a
    observed = diff.mean()
    signs = rng.choice([-1.0, 1.0], size=(RESAMPLES, len(diff)))
    null = (signs * diff).mean(axis=1)
    p_value = (np.sum(np.abs(null) >= abs(observed) - 1e-12) + 1) / (RESAMPLES + 1)
    low, high = bootstrap_ci(diff, rng)
    return {
        "mean_diff": float(observed),
        "ci95": [low, high],
        "p_value": float(p_value),
        "better": int(np.sum(diff > 1e-12)),
        "same": int(np.sum(np.abs(diff) <= 1e-12)),
        "worse": int(np.sum(diff < -1e-12)),
    }


def analyze(run: Path) -> dict:
    data = load_run(run)
    rng = np.random.default_rng(SEED)
    rows = {label: [per_question(row) for row in data[label]] for label in LABELS}
    metric_keys = {
        "recall_at_5": "recall5",
        "recall_at_10": "recall10",
        "mrr_at_10": "rr",
        "hit_rate_at_5": "hit5",
        "faithfulness": "faith",
    }
    configs = {}
    for label in LABELS:
        entry = {}
        for name, key in metric_keys.items():
            values = np.array([item[key] for item in rows[label]], dtype=float)
            entry[name] = {
                "mean": float(values.mean()),
                "ci95": list(bootstrap_ci(values, rng)),
            }
        ranks = [item["rank"] for item in rows[label]]
        entry["rank_buckets"] = {
            "1": sum(r == 1 for r in ranks if r),
            "2-3": sum(2 <= r <= 3 for r in ranks if r),
            "4-5": sum(4 <= r <= 5 for r in ranks if r),
            "6-10": sum(6 <= r <= 10 for r in ranks if r),
            "miss": sum(r is None or r > 10 for r in ranks),
        }
        entry["perfect_faithfulness"] = sum(
            item["faith"] == 1.0 for item in rows[label]
        )
        entry["answers_with_citations"] = int(
            sum(item["cited"] for item in rows[label])
        )
        entry["invalid_citation_markers"] = sum(
            item["invalid_citations"] for item in rows[label]
        )
        entry["median_answer_chars"] = statistics.median(
            item["answer_chars"] for item in rows[label]
        )
        configs[label] = entry

    comparisons = {}
    pairs = {
        "window_vs_heading__no_rerank": ("heading_rerank_off", "window_rerank_off"),
        "window_vs_heading__rerank": ("heading_rerank_on", "window_rerank_on"),
        "rerank_vs_none__heading": ("heading_rerank_off", "heading_rerank_on"),
        "rerank_vs_none__window": ("window_rerank_off", "window_rerank_on"),
    }
    for name, (base, other) in pairs.items():
        comparisons[name] = {
            metric: paired(
                np.array([item[key] for item in rows[base]], dtype=float),
                np.array([item[key] for item in rows[other]], dtype=float),
                rng,
            )
            for metric, key in (
                ("mrr_at_10", "rr"),
                ("recall_at_5", "recall5"),
                ("faithfulness", "faith"),
            )
        }

    return {
        "run_id": run.name,
        "questions": len(data[LABELS[0]]),
        "bootstrap": {
            "resamples": RESAMPLES,
            "seed": SEED,
            "interval": "percentile 95%",
        },
        "configs": configs,
        "comparisons": comparisons,
        "operations": operations(run / "api-responses.jsonl"),
        "question_rows": {
            row["id"]: {
                "question": row["question"],
                "relevant_urls": row["relevant_urls"],
                **{label: rows[label][i] for label in LABELS},
            }
            for i, row in enumerate(data[LABELS[0]])
        },
        "deltas": {
            chunking: [
                rows[f"{chunking}_rerank_on"][i]["rr"]
                - rows[f"{chunking}_rerank_off"][i]["rr"]
                for i in range(len(data[LABELS[0]]))
            ]
            for chunking in ("heading", "window")
        },
    }


def operations(path: Path) -> dict:
    """Latency, tokens and cost per API operation, including retries and failures."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for line in path.read_text("utf-8").splitlines():
        record = json.loads(line)
        grouped[record["operation"]].append(record)
    pricing = json.loads((ROOT / "data/billing/pricing.json").read_text("utf-8"))
    output = {}
    for operation, records in sorted(grouped.items()):
        ok = [r for r in records if r["status"] == "response"]
        durations = sorted(
            r["request_duration_seconds"]
            for r in ok
            if r.get("request_duration_seconds")
        )
        cost = 0.0
        for record in ok:
            usage = record.get("usage") or {}
            if record.get("token_cost_usd") is not None:
                cost += record["token_cost_usd"]
            else:  # the first 77 records were metered before the tariff path was fixed
                rates = pricing["providers"][record["provider"].rstrip("/")][
                    record["model"]
                ]
                cached = (usage.get("prompt_tokens_details") or {}).get(
                    "cached_tokens"
                ) or 0
                cost += (
                    (usage["prompt_tokens"] - cached) * rates["input_per_million"]
                    + cached * rates["cached_input_per_million"]
                    + usage["completion_tokens"] * rates["output_per_million"]
                ) / 1e6
        output[operation] = {
            "attempts": len(records),
            "succeeded": len(ok),
            "failed": len(records) - len(ok),
            "latency_samples": len(durations),
            "latency_p50_s": statistics.median(durations) if durations else None,
            "latency_p90_s": float(np.percentile(durations, 90)) if durations else None,
            "prompt_tokens": sum(
                (r.get("usage") or {}).get("prompt_tokens", 0) for r in ok
            ),
            "completion_tokens": sum(
                (r.get("usage") or {}).get("completion_tokens", 0) for r in ok
            ),
            "cost_usd": cost,
        }
    return output


# --------------------------------------------------------------------------- charts


def setup_fonts() -> None:
    names = {font.name for font in font_manager.fontManager.ttflist}
    family = "Inter" if "Inter" in names else "DejaVu Sans"
    plt.rcParams.update(
        {
            "font.family": family,
            "font.size": 10,
            "svg.fonttype": "path",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "figure.facecolor": "none",
            "axes.facecolor": "none",
            "savefig.facecolor": "none",
        }
    )


def style_axis(ax, theme: dict, *, xgrid: bool = True) -> None:
    ax.tick_params(colors=theme["muted"], length=0, labelsize=9)
    ax.spines["bottom"].set_color(theme["grid"])
    if xgrid:
        ax.grid(axis="x", color=theme["grid"], linewidth=1)
    ax.set_axisbelow(True)


def chunk_color(label: str, theme: dict) -> str:
    return theme["heading"] if label.startswith("heading") else theme["window"]


def save(fig, name: str, mode: str) -> None:
    target = ROOT / "docs/assets/charts" / f"{name}-{mode}.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        target,
        format="svg",
        bbox_inches="tight",
        pad_inches=0.15,
        metadata={"Date": None},
    )
    plt.close(fig)


def legend_handles(theme: dict):
    from matplotlib.patches import Patch

    return [
        Patch(color=theme["heading"], label="Heading-aware chunks"),
        Patch(color=theme["window"], label="Sliding-window chunks"),
    ]


def chart_quality(result: dict, mode: str) -> None:
    theme = THEMES[mode]
    metrics = [
        ("recall_at_5", "Recall@5"),
        ("recall_at_10", "Recall@10"),
        ("mrr_at_10", "MRR@10"),
        ("faithfulness", "Faithfulness (LLM judge)"),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(11.5, 2.9), sharey=True)
    order = list(reversed(LABELS))
    for ax, (key, title) in zip(axes, metrics, strict=True):
        for y, label in enumerate(order):
            item = result["configs"][label][key]
            ax.barh(
                y, item["mean"], height=0.56, color=chunk_color(label, theme), zorder=2
            )
            low, high = item["ci95"]
            ax.plot([low, high], [y, y], color=theme["text"], linewidth=1.2, zorder=3)
            for end in (low, high):
                ax.plot(
                    [end, end],
                    [y - 0.12, y + 0.12],
                    color=theme["text"],
                    linewidth=1.2,
                    zorder=3,
                )
            ax.text(
                0.03,
                y,
                f"{item['mean']:.3f}",
                va="center",
                ha="left",
                fontsize=8.5,
                color="#ffffff",
                fontweight="semibold",
                zorder=4,
            )
        ax.set_xlim(0, 1.0)
        ax.set_xticks([0, 0.5, 1.0])
        ax.set_title(
            title, color=theme["text"], fontsize=10, loc="left", fontweight="semibold"
        )
        style_axis(ax, theme)
    axes[0].set_yticks(
        range(len(order)), [PRETTY[label] for label in order], color=theme["text"]
    )
    fig.legend(
        handles=legend_handles(theme),
        loc="upper left",
        bbox_to_anchor=(0.0, 1.08),
        ncol=2,
        frameon=False,
        labelcolor=theme["text"],
        fontsize=9,
    )
    fig.text(
        0.0,
        -0.06,
        "Bars: mean over 50 questions. Whiskers: 95% bootstrap CI (10k resamples).",
        color=theme["muted"],
        fontsize=8.5,
    )
    fig.subplots_adjust(wspace=0.12)
    save(fig, "quality", mode)


def chart_rank_distribution(result: dict, mode: str) -> None:
    theme = THEMES[mode]
    buckets = ["1", "2-3", "4-5", "6-10", "miss"]
    names = ["Rank 1", "Rank 2–3", "Rank 4–5", "Rank 6–10", "Not in top 10"]
    colors = [*theme["ramp"], theme["neutral"]]
    fig, ax = plt.subplots(figsize=(9.5, 2.6))
    order = list(reversed(LABELS))
    for y, label in enumerate(order):
        left = 0
        for bucket, color in zip(buckets, colors, strict=True):
            count = result["configs"][label]["rank_buckets"][bucket]
            if count:
                ax.barh(
                    y,
                    count - 0.25,
                    left=left + 0.125,
                    height=0.56,
                    color=color,
                    zorder=2,
                )
                if count >= 3:
                    dark_fill = colors.index(color) in (
                        (0, 1) if mode == "light" else (2, 3)
                    )
                    ax.text(
                        left + count / 2,
                        y,
                        str(count),
                        ha="center",
                        va="center",
                        fontsize=8.5,
                        color="#ffffff" if dark_fill else "#191919",
                        zorder=3,
                    )
            left += count
    ax.set_xlim(0, result["questions"])
    ax.set_yticks(
        range(len(order)), [PRETTY[label] for label in order], color=theme["text"]
    )
    ax.set_xlabel(
        "Questions (first relevant page position in the top 10)",
        color=theme["muted"],
        fontsize=9,
    )
    style_axis(ax, theme, xgrid=False)
    from matplotlib.patches import Patch

    ax.legend(
        handles=[Patch(color=c, label=n) for c, n in zip(colors, names, strict=True)],
        loc="upper left",
        bbox_to_anchor=(0, 1.22),
        ncol=5,
        frameon=False,
        labelcolor=theme["text"],
        fontsize=9,
    )
    save(fig, "rank-distribution", mode)


def chart_rerank_effect(result: dict, mode: str) -> None:
    theme = THEMES[mode]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 3.0), sharey=True)
    for ax, chunking in zip(axes, ("heading", "window"), strict=True):
        deltas = sorted(result["deltas"][chunking], reverse=True)
        colors = [
            theme["good"]
            if d > 1e-9
            else theme["bad"]
            if d < -1e-9
            else theme["neutral"]
            for d in deltas
        ]
        heights = [d if abs(d) > 1e-9 else 0.012 for d in deltas]
        ax.bar(range(len(deltas)), heights, width=0.72, color=colors, zorder=2)
        ax.axhline(0, color=theme["muted"], linewidth=1, zorder=3)
        stats = result["comparisons"][f"rerank_vs_none__{chunking}"]["mrr_at_10"]
        ax.set_title(
            f"{chunking.title()} chunks\n{stats['better']} better · {stats['same']} same · "
            f"{stats['worse']} worse · ΔMRR {stats['mean_diff']:+.3f} (p = {stats['p_value']:.2f})",
            color=theme["text"],
            fontsize=9.5,
            loc="left",
            fontweight="semibold",
        )
        ax.set_xticks([])
        ax.set_ylim(-1, 1)
        ax.set_yticks([-1, -0.5, 0, 0.5, 1])
        ax.grid(axis="y", color=theme["grid"], linewidth=1)
        ax.tick_params(colors=theme["muted"], length=0, labelsize=9)
        ax.spines["bottom"].set_visible(False)
        ax.set_axisbelow(True)
    axes[0].set_ylabel(
        "Δ reciprocal rank\n(rerank − no rerank)", color=theme["muted"], fontsize=9
    )
    fig.text(
        0.0,
        -0.04,
        "One bar per question, sorted. Blue: reranker moved the first relevant page up; "
        "red: down; gray: unchanged.",
        color=theme["muted"],
        fontsize=8.5,
    )
    fig.subplots_adjust(wspace=0.08)
    save(fig, "rerank-effect", mode)


def chart_audit_impact(legacy: dict, result: dict, mode: str) -> None:
    theme = THEMES[mode]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 2.9), sharey=True)
    order = list(reversed(LABELS))
    for ax, (key, title) in zip(
        axes, (("recall_at_5", "Recall@5"), ("mrr_at_10", "MRR@10")), strict=True
    ):
        for y, label in enumerate(order):
            before = legacy[label][key]
            after = result["configs"][label][key]["mean"]
            ax.plot(
                [before, after],
                [y, y],
                color=theme["grid"],
                linewidth=2,
                zorder=1,
                solid_capstyle="round",
            )
            ax.scatter(
                [before],
                [y],
                s=64,
                color=theme["neutral"],
                zorder=2,
                edgecolors=theme["text"] if mode == "dark" else "#ffffff",
                linewidths=0,
            )
            ax.scatter([after], [y], s=64, color=chunk_color(label, theme), zorder=3)
            ax.text(
                after + (0.025 if after >= before else -0.025),
                y + 0.22,
                f"{after:.2f}",
                ha="left" if after >= before else "right",
                va="center",
                fontsize=8.5,
                color=theme["text"],
            )
            ax.text(
                before + (-0.025 if after >= before else 0.025),
                y + 0.22,
                f"{before:.2f}",
                ha="right" if after >= before else "left",
                va="center",
                fontsize=8.5,
                color=theme["muted"],
            )
        ax.set_xlim(0.2, 1.05)
        ax.set_ylim(-0.5, len(order) - 0.2)
        ax.set_title(
            title,
            color=theme["text"],
            fontsize=10,
            loc="left",
            fontweight="semibold",
            pad=10,
        )
        style_axis(ax, theme)
    axes[0].set_yticks(
        range(len(order)), [PRETTY[label] for label in order], color=theme["text"]
    )
    from matplotlib.lines import Line2D

    handles = [
        Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            color=theme["neutral"],
            markersize=8,
            label="v1 – character windows (audited)",
        ),
        Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            color=theme["heading"],
            markersize=8,
            label="v4 – token-aware chunks, heading",
        ),
        Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            color=theme["window"],
            markersize=8,
            label="v4 – token-aware chunks, window",
        ),
    ]
    fig.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(0.0, 1.08),
        ncol=3,
        frameon=False,
        labelcolor=theme["text"],
        fontsize=9,
    )
    fig.subplots_adjust(wspace=0.08)
    save(fig, "audit-impact", mode)


# --------------------------------------------------------------------------- output


def write_tables(result: dict) -> None:
    out = ROOT / "docs/results"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "metrics.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["configuration", "metric", "mean", "ci95_low", "ci95_high"])
        for label, entry in result["configs"].items():
            for metric in (
                "recall_at_5",
                "recall_at_10",
                "mrr_at_10",
                "hit_rate_at_5",
                "faithfulness",
            ):
                item = entry[metric]
                writer.writerow(
                    [
                        label,
                        metric,
                        f"{item['mean']:.4f}",
                        f"{item['ci95'][0]:.4f}",
                        f"{item['ci95'][1]:.4f}",
                    ]
                )
    with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "id",
                "question",
                *[f"{label}_rank" for label in LABELS],
                *[f"{label}_faithfulness" for label in LABELS],
            ]
        )
        for qid, row in result["question_rows"].items():
            writer.writerow(
                [
                    qid,
                    row["question"],
                    *[row[label]["rank"] or "" for label in LABELS],
                    *[row[label]["faith"] for label in LABELS],
                ]
            )
    public = {
        key: value
        for key, value in result.items()
        if key not in ("question_rows", "deltas")
    }
    (out / "analysis.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN
    run = ROOT / "eval/results" / run_id
    result = analyze(run)
    legacy_path = ROOT / "eval/results" / LEGACY_RUN / "audit/summary.json"
    legacy = {
        f"{m['strategy']}_rerank_{'on' if m['reranker'] else 'off'}": m
        for m in json.loads(legacy_path.read_text("utf-8"))["metrics"]
    }
    write_tables(result)
    setup_fonts()
    for mode in THEMES:
        chart_quality(result, mode)
        chart_rank_distribution(result, mode)
        chart_rerank_effect(result, mode)
        chart_audit_impact(legacy, result, mode)
    print(
        json.dumps(
            {k: result[k] for k in ("configs", "comparisons", "operations")}, indent=1
        )
    )


if __name__ == "__main__":
    main()
