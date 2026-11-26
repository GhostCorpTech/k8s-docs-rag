from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .access import AccessStore
from .config import get_settings
from .costs import summarize_costs
from .database import Database
from .estimation import estimate_evaluation_cost
from .evaluation import audit_evaluation, resume_evaluation, run_evaluation
from .indexer import Indexer
from .snapshot import export_index, restore_index
from .validation import validate_chunks
from .verification import repair_vectors, verify_index

app = typer.Typer(no_args_is_help=True, help="Kubernetes documentation RAG utilities")
console = Console()


@app.command()
def index(
    strategy: Annotated[str, typer.Option(help="heading, window or both")] = "both",
    sync: Annotated[
        bool, typer.Option("--sync/--no-sync", help="Clone or pull kubernetes/website")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the delta without changing indexes")
    ] = False,
) -> None:
    """Build or incrementally update one or both indexes."""
    if strategy not in {"heading", "window", "both"}:
        raise typer.BadParameter("strategy must be heading, window or both")
    settings = get_settings()
    indexer = Indexer(settings)
    if sync:
        commit = indexer.sync()
        console.print(f"Documentation is at commit [bold]{commit}[/bold]")
    strategies = ("heading", "window") if strategy == "both" else (strategy,)
    for selected in strategies:
        stats = indexer.run(selected, dry_run=dry_run)  # type: ignore[arg-type]
        console.print(
            f"[bold]{selected}[/bold]: total={stats.total_pages}, added={stats.added}, "
            f"changed={stats.changed}, deleted={stats.deleted}, "
            f"unchanged={stats.unchanged}, chunks_written={stats.chunks_written}"
        )


@app.command()
def evaluate(
    with_faithfulness: Annotated[
        bool,
        typer.Option(
            "--with-faithfulness/--no-faithfulness",
            help="Generate answers and run the LLM faithfulness judge",
        ),
    ] = False,
    workers: Annotated[
        int, typer.Option("--workers", min=1, max=4, help="Maximum parallel questions")
    ] = 1,
) -> None:
    """Compare both chunkers with and without reranking on the 50-question set."""
    output = run_evaluation(get_settings(), with_faithfulness=with_faithfulness, workers=workers)
    console.print(f"Results: [bold]{output}[/bold]")


@app.command("resume-evaluation")
def resume_evaluation_command(
    run_id: Annotated[
        str,
        typer.Argument(help="Existing directory name under eval/results"),
    ],
    workers: Annotated[
        int, typer.Option("--workers", min=1, max=4, help="Maximum parallel questions")
    ] = 1,
) -> None:
    """Resume a manifest-backed evaluation without repeating completed stages."""
    output = resume_evaluation(
        get_settings(), get_settings().eval_path.parent / "results" / run_id, workers=workers
    )
    console.print(f"Results: [bold]{output}[/bold]")


@app.command("audit-evaluation")
def audit_evaluation_command(
    run_id: Annotated[str, typer.Argument(help="Directory name under eval/results")],
) -> None:
    """Recompute saved metrics without API calls or modifying original results."""
    output = audit_evaluation(get_settings(), get_settings().eval_path.parent / "results" / run_id)
    console.print(f"Audit: [bold]{output}[/bold]")


@app.command()
def estimate_cost(
    run_id: Annotated[
        str, typer.Argument(help="Historical complete run to estimate request sizes")
    ],
) -> None:
    """Estimate a future full eval offline at configured router prices."""
    import json

    settings = get_settings()
    historical = settings.eval_path.parent / "results" / run_id
    payload = estimate_evaluation_cost(settings, historical)
    output = settings.eval_path.parent.parent / "data/billing/evaluation-estimate.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print_json(data=payload)


@app.command()
def evaluation_cost(
    run_id: Annotated[str, typer.Argument(help="Directory name under eval/results")],
) -> None:
    """Show metered API token cost, including retries, without making API calls."""
    import json

    settings = get_settings()
    run = settings.eval_path.parent / "results" / run_id
    if not run.is_dir():
        raise typer.BadParameter("Evaluation run not found")
    payload = summarize_costs(run / "api-responses.jsonl", settings.api_pricing_path)
    (run / "costs.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    console.print_json(data=payload)


@app.command()
def validate_chunking() -> None:
    """Validate all chunks offline with the cached embedding tokenizer."""
    import json

    settings = get_settings()
    payload = validate_chunks(settings)
    output = settings.eval_path.parent.parent / ".tmp" / "chunking-v4-validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print_json(data=payload)
    console.print(f"Validation: {output}")


@app.command("verify-index")
def verify_index_command(
    sample: Annotated[
        int, typer.Option(help="Chunks per strategy to re-embed and compare (0 = skip)")
    ] = 100,
) -> None:
    """Check a restored or rebuilt index against the docs source, SQLite and Qdrant."""
    report = verify_index(get_settings(), sample=sample)
    console.print_json(data=report)
    if not report["ok"]:
        raise typer.Exit(code=1)


@app.command("repair-vectors")
def repair_vectors_command(
    strategy: Annotated[str, typer.Option(help="heading, window or both")] = "both",
) -> None:
    """Re-embed vectors that are not unit-norm from stored chunk text (no re-chunking)."""
    strategies = ("heading", "window") if strategy == "both" else (strategy,)
    for selected in strategies:
        console.print(repair_vectors(get_settings(), selected, log=console.print))


@app.command("export-index")
def export_index_command(
    archive: Annotated[str, typer.Argument(help="Output .zip path")],
) -> None:
    """Package both Qdrant collections and the SQLite index into one verified archive."""
    from pathlib import Path

    manifest = export_index(get_settings(), Path(archive))
    console.print_json(data={k: v for k, v in manifest.items() if k != "files"})
    console.print(f"Archive: [bold]{archive}[/bold]")


@app.command("restore-index")
def restore_index_command(
    archive: Annotated[str, typer.Argument(help="Archive created by export-index")],
    force: Annotated[bool, typer.Option("--force", help="Replace an existing index")] = False,
) -> None:
    """Restore a packaged index (e.g. from a GitHub Release) instead of re-embedding."""
    from pathlib import Path

    console.print_json(data=restore_index(get_settings(), Path(archive), force=force))
    console.print("Restored. Optional: `index --sync` updates it to the latest docs incrementally.")


# ---------------------------------------------------------------- demo access keys
def _store() -> AccessStore:
    return AccessStore(get_settings().access_db_path)


def _date(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


@app.command("create-key")
def create_key(
    label: Annotated[str, typer.Option(help="Who the key is for, e.g. 'hh: Company X'")],
    days: Annotated[int | None, typer.Option(min=1, max=365, help="Validity in days")] = None,
    daily_limit: Annotated[
        int | None, typer.Option(min=1, max=10_000, help="Questions per UTC day")
    ] = None,
) -> None:
    """Issue a personal demo key. The key is shown once; only its hash is stored."""
    settings = get_settings()
    key, secret = _store().create(
        label,
        days=days or settings.demo_key_days,
        daily_limit=daily_limit or settings.demo_key_daily_limit,
    )
    console.print(f"Key [bold]#{key.id}[/bold] for {key.label!r}")
    console.print(f"  valid until {_date(key.expires_at)} UTC, {key.daily_limit} questions/day")
    console.print(f"  key:  [bold green]{secret}[/bold green]")
    if settings.demo_public_url:
        console.print(f"  link: {settings.demo_public_url.rstrip('/')}/#/?key={secret}")
    console.print("[dim]Shown once. Revoke with: revoke-key " + str(key.id) + "[/dim]")


@app.command("list-keys")
def list_keys() -> None:
    """All demo keys with state, today's usage and totals."""
    store = _store()
    now = store.clock()
    table = Table("ID", "Key", "Label", "State", "Today", "Total", "Last used", "Expires")
    for key, today in store.list():
        table.add_row(
            str(key.id),
            key.hint,
            key.label,
            key.state(now),
            f"{today}/{key.daily_limit}",
            str(key.total_questions),
            _date(key.last_used_at),
            key.expires_at.strftime("%Y-%m-%d"),
        )
    console.print(table)
    settings = get_settings()
    console.print(
        f"Server today: {store.used_today()}/{settings.demo_global_daily_limit} questions (UTC day)"
    )


@app.command("revoke-key")
def revoke_key(key_id: Annotated[int, typer.Argument(help="ID from list-keys")]) -> None:
    """Revoke a key immediately; its next request is refused."""
    try:
        key = _store().revoke(key_id)
    except KeyError as error:
        raise typer.BadParameter(str(error)) from error
    console.print(f"Key #{key.id} ({key.label}) revoked at {_date(key.revoked_at)} UTC")


@app.command("extend-key")
def extend_key(
    key_id: Annotated[int, typer.Argument(help="ID from list-keys")],
    days: Annotated[int, typer.Option(min=1, max=365, help="Valid for N days from now")] = 30,
    daily_limit: Annotated[int | None, typer.Option(min=1, max=10_000)] = None,
) -> None:
    """Extend an active or expired key (and optionally change its daily limit)."""
    try:
        key = _store().extend(key_id, days, daily_limit)
    except (KeyError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    console.print(
        f"Key #{key.id} valid until {_date(key.expires_at)} UTC, {key.daily_limit} questions/day"
    )


@app.command()
def status() -> None:
    """Show local index state."""
    state = Database(get_settings().sqlite_path).status()
    table = Table("Strategy", "Pages", "Chunks", "Last indexed")
    for strategy, item in state.items():
        table.add_row(
            strategy,
            str(item["pages"]),
            str(item["chunks"]),
            str(item["last_indexed"] or "never"),
        )
    console.print(table)


if __name__ == "__main__":
    app()
