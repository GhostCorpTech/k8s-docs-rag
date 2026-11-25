from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .config import get_settings
from .costs import summarize_costs
from .database import Database
from .estimation import estimate_evaluation_cost
from .evaluation import audit_evaluation, resume_evaluation, run_evaluation
from .indexer import Indexer
from .validation import validate_chunks

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
