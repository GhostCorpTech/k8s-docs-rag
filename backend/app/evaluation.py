import hashlib
import json
import math
import os
import statistics
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from urllib.parse import urldefrag

from rich.console import Console
from rich.table import Table

from .cloud import CloudModels
from .config import Settings
from .costs import summarize_costs
from .database import Database
from .docs import current_commit, load_pages
from .indexer import CHUNKER_VERSION, index_hash
from .local_embeddings import get_local_model
from .retrieval import Retriever
from .vector_store import VectorStore

console = Console()
EVALUATION_VERSION = "2026-11-v2"
LABELS = [
    (strategy, rerank, f"{strategy}_rerank_{'on' if rerank else 'off'}")
    for strategy in ("heading", "window")
    for rerank in (False, True)
]


@dataclass
class Metrics:
    strategy: str
    reranker: bool
    questions: int
    recall_at_5: float
    recall_at_10: float
    mrr_at_10: float
    faithfulness: float | None
    hit_rate_at_5: float
    hit_rate_at_10: float
    faithfulness_questions: int


def _page_url(url: str) -> str:
    return urldefrag(url.strip())[0].rstrip("/") + "/"


def _rank_of_relevant(retrieved: list[str], relevant: list[str]) -> int | None:
    relevant_pages = {_page_url(url) for url in relevant}
    return next(
        (rank for rank, url in enumerate(retrieved, 1) if _page_url(url) in relevant_pages), None
    )


def _recall(retrieved: list[str], relevant: list[str], k: int) -> float:
    gold = {_page_url(url) for url in relevant}
    return len({_page_url(url) for url in retrieved[:k]} & gold) / len(gold)


def load_questions(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as file:
        questions = [json.loads(line) for line in file if line.strip()]
    if not questions:
        raise ValueError(f"No questions found in {path}")
    ids = set()
    for item in questions:
        if not isinstance(item.get("id"), str) or not item["id"] or item["id"] in ids:
            raise ValueError("Question IDs must be nonempty and unique")
        ids.add(item["id"])
        if not isinstance(item.get("question"), str) or not item["question"].strip():
            raise ValueError(f"Question text missing for {item['id']}")
        urls = item.get("relevant_urls")
        if (
            not isinstance(urls, list)
            or not urls
            or not all(
                isinstance(url, str) and url.startswith("https://kubernetes.io/") for url in urls
            )
        ):
            raise ValueError(f"Relevant Kubernetes URLs missing for {item['id']}")
    return questions


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temp.replace(path)


@contextmanager
def _run_lock(result_dir: Path):
    """OS lock releases on process death; a leftover filename is not a stale lock."""
    with (result_dir / ".evaluation.lock").open("a+b") as file:
        file.seek(0, os.SEEK_END)
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("This evaluation is already running") from error
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _read_records(path: Path, *, repair_tail: bool = False) -> list[dict]:
    if not path.exists():
        return []
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    records, offset = [], 0
    for index, line in enumerate(lines):
        try:
            if line.strip():
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("JSONL record must be an object")
                records.append(record)
        except (ValueError, UnicodeDecodeError) as error:
            if repair_tail and index == len(lines) - 1 and not line.endswith(b"\n"):
                backup = path.with_name(
                    path.name + f".interrupted-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
                )
                backup.write_bytes(data)
                path.write_bytes(data[:offset])
                console.print(f"Preserved interrupted tail in {backup.name}")
                break
            raise ValueError(f"Invalid evaluation JSONL: {path.name}, line {index + 1}") from error
        offset += len(line)
    return records


def _validate_records(records: list[dict], questions: list[dict], with_faithfulness: bool) -> None:
    expected = {item["id"]: item for item in questions}
    seen = set()
    for record in records:
        question_id = record.get("id")
        if question_id not in expected or question_id in seen:
            raise ValueError("Saved evaluation contains unknown or duplicate question IDs")
        seen.add(question_id)
        if any(record.get(key) != value for key, value in expected[question_id].items()):
            raise ValueError(f"Question/reference changed for {question_id}")
        if "hits" not in record:
            raise ValueError(f"Saved retrieval missing for {question_id}")
        if not isinstance(record["hits"], list):
            raise TypeError(f"Saved retrieval must be a list for {question_id}")
        if with_faithfulness:
            score = record.get("faithfulness")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or not 0 <= score <= 1
            ):
                raise ValueError(f"Invalid saved faithfulness for {question_id}")
            if (
                not isinstance(record.get("generated_answer"), str)
                or not record["generated_answer"].strip()
            ):
                raise ValueError(f"Saved answer missing for {question_id}")


def metrics_from_records(records: list[dict], strategy: str, reranker: bool) -> Metrics:
    if not records:
        raise ValueError("Cannot compute metrics for an empty configuration")
    urls = [[hit["url"] for hit in record["hits"]] for record in records]
    ranks = [
        _rank_of_relevant(found, record["relevant_urls"])
        for found, record in zip(urls, records, strict=True)
    ]
    faith = [
        float(record["faithfulness"])
        for record in records
        if record.get("faithfulness") is not None
    ]
    return Metrics(
        strategy=strategy,
        reranker=reranker,
        questions=len(records),
        recall_at_5=statistics.mean(
            _recall(found, record["relevant_urls"], 5)
            for found, record in zip(urls, records, strict=True)
        ),
        recall_at_10=statistics.mean(
            _recall(found, record["relevant_urls"], 10)
            for found, record in zip(urls, records, strict=True)
        ),
        mrr_at_10=sum(1 / rank for rank in ranks if rank is not None and rank <= 10) / len(records),
        faithfulness=statistics.mean(faith) if faith else None,
        hit_rate_at_5=sum(rank is not None and rank <= 5 for rank in ranks) / len(records),
        hit_rate_at_10=sum(rank is not None and rank <= 10 for rank in ranks) / len(records),
        faithfulness_questions=len(faith),
    )


def evaluate_configuration(
    settings: Settings,
    questions: list[dict],
    strategy: str,
    reranker: bool,
    with_faithfulness: bool,
    output_path: Path,
    workers: int = 1,
) -> Metrics:
    records = _read_records(output_path, repair_tail=True)
    _validate_records(records, questions, with_faithfulness)
    completed = {record["id"] for record in records}
    pending = [item for item in questions if item["id"] not in completed]
    if not pending:
        return metrics_from_records(records, strategy, reranker)
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    if workers > 1 and settings.embedding_provider == "local":
        # Warm before threads start: lru_cache alone does not prevent duplicate first loads.
        get_local_model(
            settings.local_embedding_model,
            str(settings.model_cache_path),
            settings.local_embedding_threads,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # A complete but unterminated final JSON line must not concatenate with the next record.
    needs_newline = (
        output_path.exists()
        and output_path.stat().st_size > 0
        and not output_path.read_bytes().endswith(b"\n")
    )
    with output_path.open("a", encoding="utf-8") as output:
        if needs_newline:
            output.write("\n")
        for record in _parallel_questions(
            settings, pending, strategy, reranker, with_faithfulness, output_path, workers
        ):
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
            records.append(record)
            console.print(
                f"[{strategy}, rerank={reranker}, workers={workers}] {len(records)}/{len(questions)} id={record['id']} rank={record['rank'] or '-'}"
            )
    return metrics_from_records(records, strategy, reranker)


def _evaluate_question(
    settings: Settings,
    item: dict,
    strategy: str,
    reranker: bool,
    with_faithfulness: bool,
    output_path: Path,
    stop: Event,
) -> dict:
    checkpoint = (
        output_path.parent
        / "checkpoints"
        / output_path.stem
        / f"{hashlib.sha256(item['id'].encode()).hexdigest()}.json"
    )
    record = (
        json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else dict(item)
    )
    if any(record.get(key) != value for key, value in item.items()):
        raise ValueError(f"Checkpoint question changed for {item['id']}")
    cloud = CloudModels(settings)
    cloud.log_context = {"configuration": output_path.stem, "question_id": item["id"]}
    cloud.stop_event = stop

    def check_stop() -> None:
        if stop.is_set():
            raise RuntimeError("Evaluation stopped; completed API stages were checkpointed")

    if "hits" not in record:
        check_stop()
        retriever = Retriever(settings)
        record["hits"] = retriever.search(
            item["question"], strategy=strategy, rerank=reranker, limit=10, models=cloud
        )
        record["rank"] = _rank_of_relevant(
            [hit["url"] for hit in record["hits"]], item["relevant_urls"]
        )
        record["lexical_query"] = retriever.database.cached_lexical_query(item["question"])
        _atomic_json(checkpoint, record)
    if with_faithfulness:
        if "generated_answer" not in record:
            check_stop()
            record["answer_context"] = cloud.context_hits(record["hits"][: settings.final_limit])
            record["generated_answer"] = cloud.answer(item["question"], record["answer_context"])
            _atomic_json(checkpoint, record)
        if "faithfulness" not in record:
            check_stop()
            record["faithfulness"] = cloud.faithfulness(
                record["generated_answer"], [hit["content"] for hit in record["answer_context"]]
            )
            _atomic_json(checkpoint, record)
    _validate_records([record], [item], with_faithfulness)
    return record


def _parallel_questions(
    settings: Settings,
    pending: list[dict],
    strategy: str,
    reranker: bool,
    with_faithfulness: bool,
    output_path: Path,
    workers: int,
):
    """Bound submissions to workers; one coordinator writes JSONL, no queued paid backlog."""
    stop = Event()
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rag-eval")
    items = iter(pending)
    futures = set()

    def execute_item(item: dict) -> dict:
        try:
            return _evaluate_question(
                settings, item, strategy, reranker, with_faithfulness, output_path, stop
            )
        except BaseException:
            stop.set()
            raise

    def submit_next() -> None:
        if stop.is_set():
            return
        item = next(items, None)
        if item is not None:
            futures.add(executor.submit(execute_item, item))

    try:
        for _ in range(min(workers, len(pending))):
            submit_next()
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                error = future.exception()
                if error is not None:
                    raise error
            for future in done:
                futures.remove(future)
                yield future.result()
                submit_next()
    except BaseException:
        stop.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        # In-flight calls may finish, save their stage and stop before the next API call.
        executor.shutdown(wait=True, cancel_futures=True)


def _index_identity(settings: Settings) -> dict:
    pages = load_pages(settings.docs_repo_path)
    database = Database(settings.sqlite_path)
    status = database.status()
    hashes = {}
    for strategy in ("heading", "window"):
        expected = {
            doc_id: index_hash(page.content_hash, strategy, settings.embedding_identity())
            for doc_id, page in pages.items()
        }
        actual = database.page_hashes(strategy)
        if not pages or actual != expected:
            raise RuntimeError(
                f"Evaluation requires complete indexes matching current source/chunker/embeddings ({strategy}={len(actual)}/{len(pages)}). Finish `index --strategy both` first."
            )
        hashes[strategy] = _digest(actual)
    vectors = VectorStore(settings)
    for strategy in ("heading", "window"):
        if (
            not vectors.exists(strategy)
            or vectors.client.get_collection(settings.collection(strategy)).points_count
            != status[strategy]["chunks"]
        ):
            raise RuntimeError(f"Qdrant/SQLite chunk counts differ for {strategy}")
    return {
        "source_commit": current_commit(settings.docs_repo_path),
        "chunker_version": CHUNKER_VERSION,
        "page_hashes": hashes,
        "counts": {
            key: {field: value[field] for field in ("pages", "chunks")}
            for key, value in status.items()
        },
    }


def _configuration(settings: Settings) -> dict:
    return {
        "api_base_url": settings.openai_base_url,
        "chat_model": settings.chat_model,
        "rerank_model": settings.rerank_model,
        "judge_model": settings.judge_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.active_embedding_model(),
        "embedding_base_url": (settings.embedding_base_url or settings.openai_base_url)
        if settings.embedding_provider == "openai"
        else None,
        "collection_prefix": settings.collection_prefix,
        **{
            key: getattr(settings, key)
            for key in (
                "dense_limit",
                "lexical_limit",
                "final_limit",
                "rrf_k",
                "rrf_dense_weight",
                "rrf_lexical_weight",
                "max_context_chars",
            )
        },
        "lexical_query_policy": "shared_sqlite_cache; exact translated query stored per record",
    }


def run_evaluation(settings: Settings, with_faithfulness: bool = False, workers: int = 1) -> Path:
    identity = _index_identity(settings)
    questions = load_questions(settings.eval_path)
    result_dir = (
        settings.eval_path.parent / "results" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    result_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "run_id": result_dir.name,
        "created_at": datetime.now(UTC).isoformat(),
        "evaluation_version": EVALUATION_VERSION,
        "with_faithfulness": with_faithfulness,
        "configuration": _configuration(settings),
        "index": identity,
        "questions_sha256": _digest(questions),
        "question_count": len(questions),
        "execution": {"workers": workers},
    }
    _atomic_json(result_dir / "manifest.json", manifest)
    (result_dir / "questions.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in questions), encoding="utf-8"
    )
    with _run_lock(result_dir):
        return _run_configurations(settings, questions, manifest, result_dir, workers)


def resume_evaluation(settings: Settings, result_dir: Path, workers: int = 1) -> Path:
    if not result_dir.is_absolute():
        result_dir = settings.eval_path.parent / "results" / result_dir
    if not result_dir.is_dir():
        raise FileNotFoundError(f"Evaluation run directory not found: {result_dir}")
    with _run_lock(result_dir):
        manifest_path = result_dir / "manifest.json"
        if not manifest_path.exists():
            raise ValueError(
                "Legacy run has no provenance manifest; use audit-evaluation without API calls. It cannot be safely resumed with current settings."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        questions = load_questions(settings.eval_path)
        if (
            manifest["evaluation_version"] != EVALUATION_VERSION
            or manifest["configuration"] != _configuration(settings)
            or manifest["questions_sha256"] != _digest(questions)
        ):
            raise ValueError(
                "Cannot resume: provider/models, retrieval settings, dataset or evaluation version changed. Start a separate run."
            )
        if manifest["index"] != _index_identity(settings):
            raise ValueError("Cannot resume: index/source changed. Start a separate run.")
        manifest.setdefault("execution_history", []).append(
            {
                "at": datetime.now(UTC).isoformat(),
                "previous_workers": manifest.get("execution", {}).get("workers", 1),
                "workers": workers,
                "action": "resume",
            }
        )
        manifest["execution"] = {"workers": workers}
        _atomic_json(manifest_path, manifest)
        return _run_configurations(settings, questions, manifest, result_dir, workers)


def _run_configurations(
    settings: Settings, questions: list[dict], manifest: dict, result_dir: Path, workers: int = 1
) -> Path:
    settings = settings.model_copy(
        update={"api_response_log_path": result_dir / "api-responses.jsonl"}
    )
    metrics = [
        evaluate_configuration(
            settings,
            questions,
            strategy,
            rerank,
            manifest["with_faithfulness"],
            result_dir / f"{label}.jsonl",
            workers=workers,
        )
        for strategy, rerank, label in LABELS
    ]
    payload = {
        **manifest,
        "completed_at": datetime.now(UTC).isoformat(),
        "metrics": [asdict(item) for item in metrics],
        "billing": summarize_costs(result_dir / "api-responses.jsonl", settings.api_pricing_path),
    }
    _atomic_json(result_dir / "summary.json", payload)
    _atomic_json(result_dir / "costs.json", payload["billing"])
    write_markdown(result_dir / "summary.md", payload)
    print_metrics(metrics)
    return result_dir


def audit_evaluation(settings: Settings, result_dir: Path) -> Path:
    """Recompute retrieval metrics offline, preserving every original result file."""
    if not result_dir.is_absolute():
        result_dir = settings.eval_path.parent / "results" / result_dir
    original = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))
    questions_path = result_dir / "questions.jsonl"
    questions = load_questions(questions_path if questions_path.exists() else settings.eval_path)
    metrics, suspicious = [], []
    legacy = original.get("evaluation_version") != EVALUATION_VERSION
    for strategy, rerank, label in LABELS:
        records = _read_records(result_dir / f"{label}.jsonl")
        _validate_records(records, questions, original["with_faithfulness"])
        if len(records) != len(questions):
            raise ValueError(f"Incomplete configuration {label}: {len(records)}/{len(questions)}")
        for record in records:
            if legacy and record.get("faithfulness") == 0:
                suspicious.append(
                    {
                        "configuration": label,
                        "id": record["id"],
                        "reason": "zero faithfulness: genuine zero or swallowed JSON error; raw provider response was not saved",
                    }
                )
            if (
                legacy
                and rerank
                and record["hits"]
                and all(hit.get("rerank_score") == 0 for hit in record["hits"])
            ):
                suspicious.append(
                    {
                        "configuration": label,
                        "id": record["id"],
                        "reason": "all rerank scores zero: genuine tie or swallowed JSON error",
                    }
                )
        metrics.append(metrics_from_records(records, strategy, rerank))
    warnings = []
    if legacy:
        warnings = [
            "Legacy Recall was HitRate; Recall is now recomputed as macro-average fraction of relevant pages found (anchors ignored).",
            "Legacy faithfulness is unverified: judge saw up to 10 hits, generation up to 8. Errors parsing scores were silently replaced by zero.",
            "Legacy embeddings used character windows and truncated overlong E5 inputs. Do not compare this run with token-aware v4 indexes as a router-only experiment.",
            "Provider metadata was saved only at completion, not at start; original raw provider responses are unavailable.",
        ]
    payload = {
        **original,
        "evaluation_version": "legacy-audited" if legacy else EVALUATION_VERSION,
        "audited_at": datetime.now(UTC).isoformat(),
        "warnings": warnings,
        "suspicious_records": suspicious,
        "faithfulness_verified": not legacy,
        "metrics": [asdict(item) for item in metrics],
    }
    audit_dir = result_dir / "audit"
    _atomic_json(audit_dir / "summary.json", payload)
    write_markdown(audit_dir / "summary.md", payload)
    print_metrics(metrics)
    return audit_dir


def write_markdown(path: Path, payload: dict) -> None:
    lines = [
        f"# Eval run {payload['run_id']}",
        "",
        "Recall@k: mean fraction of relevant pages found in the first k chunks; duplicate pages count once.",
        "HitRate@k: fraction of questions with at least one relevant page. MRR ranks chunks; anchors are ignored.",
        "",
    ]
    lines.extend(f"> {warning}" for warning in payload.get("warnings", []))
    lines.extend(
        [
            "",
            "| Chunking | Reranker | Recall@5 | Recall@10 | MRR@10 | Faithfulness |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for metric in payload["metrics"]:
        faith = f"{metric['faithfulness']:.3f}" if metric["faithfulness"] is not None else "not run"
        lines.append(
            f"| {metric['strategy']} | {'on' if metric['reranker'] else 'off'} | {metric['recall_at_5']:.3f} | {metric['recall_at_10']:.3f} | {metric['mrr_at_10']:.3f} | {faith} |"
        )
    if payload.get("suspicious_records"):
        lines.extend(["", "## Suspicious legacy records (not proof of failure)", ""])
        lines.extend(
            f"- {item['configuration']} / {item['id']}: {item['reason']}"
            for item in payload["suspicious_records"]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_metrics(metrics: list[Metrics]) -> None:
    table = Table(title="Kubernetes RAG evaluation")
    for column in ("Chunking", "Reranker", "Recall@5", "Recall@10", "MRR@10", "Faithfulness"):
        table.add_column(column)
    for metric in metrics:
        table.add_row(
            metric.strategy,
            "on" if metric.reranker else "off",
            f"{metric.recall_at_5:.3f}",
            f"{metric.recall_at_10:.3f}",
            f"{metric.mrr_at_10:.3f}",
            f"{metric.faithfulness:.3f}" if metric.faithfulness is not None else "not run",
        )
    console.print(table)
