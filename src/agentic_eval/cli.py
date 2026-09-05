from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

from agentic_eval.config import load_run_spec
from agentic_eval.environments.aneel.build import build_corpus
from agentic_eval.environments.aneel.corpus import AneelCorpus
from agentic_eval.environments.aneel.download import download_dataset
from agentic_eval.harnesses import create_adapter
from agentic_eval.runner import EvaluationRunner
from agentic_eval.runner.exports import export_run
from agentic_eval.runner.ledger import RunLedger

app = typer.Typer(
    no_args_is_help=True,
    help="Run reproducible, trace-first agentic QA evaluations.",
)


def _run_id(name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{name}-{timestamp}-{uuid.uuid4().hex[:8]}"


@app.command("corpus-download")
def corpus_download(
    output: Annotated[
        Path,
        typer.Option(help="Ignored directory for the pinned Hugging Face snapshot."),
    ] = Path("data/raw/biblioteca-aneel-categorizado-4"),
) -> None:
    """Download and hash the pinned public ANEEL dataset snapshot."""
    try:
        result = download_dataset(output)
    except Exception as exc:
        typer.echo(f"Corpus download failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("corpus-build")
def corpus_build(
    raw_dir: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, readable=True),
    ] = Path("data/raw/biblioteca-aneel-categorizado-4"),
    output: Annotated[
        Path,
        typer.Option(help="Output SQLite corpus database."),
    ] = Path("data/processed/aneel-corpus.sqlite3"),
    limit: Annotated[
        int | None,
        typer.Option(min=1, help="Build only N rows for development."),
    ] = None,
) -> None:
    """Build normalized metadata and an FTS5 index from downloaded Parquet."""
    try:
        result = build_corpus(raw_dir, output, limit=limit)
    except Exception as exc:
        typer.echo(f"Corpus build failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("corpus-inspect")
def corpus_inspect(
    database: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True),
    ],
    query: Annotated[
        str | None,
        typer.Option(help="Optional full-text query to sample."),
    ] = None,
) -> None:
    """Inspect corpus distributions and optionally run a sample search."""
    try:
        with AneelCorpus(database) as corpus:
            result: dict[str, object] = {"stats": corpus.stats()}
            if query:
                result["results"] = corpus.search(query)
    except Exception as exc:
        typer.echo(f"Corpus inspection failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command()
def doctor(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
    skip_live_case: Annotated[
        bool, typer.Option(help="Skip the environment qualification case.")
    ] = False,
) -> None:
    """Validate OpenCode, vLLM, environment tools, and cancellation prerequisites."""
    spec = load_run_spec(config)

    async def check() -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="agentic-eval-doctor-") as temporary:
            adapter = create_adapter(spec, Path(temporary))
            try:
                return await adapter.qualify(run_live_case=not skip_live_case)
            finally:
                await adapter.close()

    try:
        result = asyncio.run(check())
    except Exception as exc:
        typer.echo(f"Qualification failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))


@app.command("run")
def run_command(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Start a new evaluation run."""
    spec = load_run_spec(config)
    run_id = _run_id(spec.name)
    run_dir = spec.output_dir / run_id
    runner = EvaluationRunner(spec, config, run_dir, run_id=run_id)
    typer.echo(f"Run directory: {run_dir}")
    try:
        summary = asyncio.run(runner.run())
    except KeyboardInterrupt as exc:
        typer.echo("Run cancelled; use resume to continue.", err=True)
        raise typer.Exit(130) from exc
    except Exception as exc:
        typer.echo(f"Run failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(summary, indent=2))


@app.command()
def resume(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Resume incomplete cases in an existing run."""
    spec = load_run_spec(config)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    runner = EvaluationRunner(
        spec,
        config,
        run_dir,
        run_id=str(manifest["run_id"]),
        resume=True,
    )
    try:
        summary = asyncio.run(runner.run())
    except KeyboardInterrupt as exc:
        typer.echo("Resume cancelled; it is safe to resume again.", err=True)
        raise typer.Exit(130) from exc
    except Exception as exc:
        typer.echo(f"Resume failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(summary, indent=2))


@app.command()
def status(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Show case counts by terminal state."""
    ledger_path = run_dir / "ledger.sqlite3"
    if not ledger_path.exists():
        typer.echo(f"No ledger found at {ledger_path}", err=True)
        raise typer.Exit(1)
    ledger = RunLedger(ledger_path)
    try:
        summary = ledger.summary()
    finally:
        ledger.close()
    typer.echo(json.dumps(summary, indent=2))


@app.command("export")
def export_command(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Export normalized final results to JSONL and CSV."""
    try:
        jsonl_path, csv_path = export_run(run_dir)
    except Exception as exc:
        typer.echo(f"Export failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote {jsonl_path}")
    typer.echo(f"Wrote {csv_path}")


if __name__ == "__main__":
    app()
