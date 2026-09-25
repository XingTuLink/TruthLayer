"""Typer CLI — thin adapter only (#31, #50, #67).

Exit codes follow #26: 0 pass, 1 fail_on threshold, 2 system/config/runtime.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from sqlalchemy.exc import SQLAlchemyError

from truthlayer import __version__
from truthlayer.db.orm import ScanRun
from truthlayer.db.session import SessionLocal
from truthlayer.detection.service import (
    DETECTOR_VERSION,
    DetectionResult,
    DriftDetectionService,
)
from truthlayer.domain.errors import TruthLayerError
from truthlayer.extraction.service import ExtractionResult
from truthlayer.ingestion.service import IngestionResult
from truthlayer.providers.factory import build_embedder, build_llm
from truthlayer.services.workspace import prepare_check

app = typer.Typer(
    name="truthlayer",
    help="Continuous QA for the knowledge your AI depends on.",
    no_args_is_help=True,
)
drift_app = typer.Typer(help="Inspect and manage drift findings.")
app.add_typer(drift_app, name="drift")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _print_version(value: bool) -> None:
    # Eager parameter callbacks fire during argument parsing, before the
    # Typer group's "missing command" handling.
    if value:
        typer.echo(f"truthlayer {__version__}")
        raise typer.Exit(code=0)


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
        callback=_print_version,
        is_eager=True,
    ),
) -> None:
    return None


def _run_ingestion(path: Path) -> IngestionResult | None:
    """Validate config then run ingestion. Returns None on fatal error."""
    try:
        preparation = prepare_check(path)
        with SessionLocal() as session:
            from truthlayer.ingestion.service import DocumentIngestionService

            service = DocumentIngestionService(
                session,
                preparation.config,
                preparation.config_path.parent,
            )
            result = service.run()
            session.commit()
        return result
    except TruthLayerError as exc:
        typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
        return None
    except SQLAlchemyError as exc:
        typer.secho(
            f"database error: {type(exc).__name__}: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        return None


def _print_ingestion_summary(result: IngestionResult) -> None:
    # Summary first, details later (#50).
    typer.echo("TruthLayer Knowledge Check")
    typer.echo(f"Workspace : {result.workspace_name}")
    typer.echo(f"Discovered: {result.discovered} document(s)")
    typer.echo(
        f"Documents : {result.parsed} parsed, {result.unchanged} unchanged, "
        f"{result.failed} failed"
    )
    typer.echo(f"Chunks    : {result.chunks}")

    for warning in result.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW)
    for relative_path, message in result.errors:
        typer.secho(
            f"error: {relative_path}: {message}",
            err=True,
            fg=typer.colors.RED,
        )

    if result.parsed == 0 and result.unchanged == 0:
        typer.secho(
            "no ingested documents; check sources configuration",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(
            "tip: run 'truthlayer scan' to extract facts and write a snapshot "
            "(drift detection arrives in Sprint 4)",
            fg=typer.colors.BLUE,
        )


@app.command()
def check(
    path: Path = typer.Argument(
        ..., exists=False, help="Workspace directory or .truthlayer.yaml file."
    ),
    html: Path | None = typer.Option(
        None, "--html", help="Write HTML report to this path (Sprint 5)."
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Write JSON report to this path (Sprint 5)."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose logging."),
) -> None:
    """Ingest and check a knowledge workspace (#31)."""
    _configure_logging(verbose)
    result = _run_ingestion(path)
    if result is None:
        raise typer.Exit(code=2)

    _print_ingestion_summary(result)

    if html is not None or output is not None:
        typer.secho(
            "note: --html/--output reports arrive in Sprint 5",
            fg=typer.colors.YELLOW,
        )

    # Parser failures are runtime errors (#26, #34).
    raise typer.Exit(code=2 if result.failed else 0)


def _print_extraction_summary(result: ExtractionResult) -> None:
    typer.echo("Knowledge Extraction")
    typer.echo(f"Scan run  : {result.scan_run_id}")
    typer.echo(
        f"Chunks    : {result.chunks_processed} processed | "
        f"entities +{result.entities_new} ({result.entities_total} total) | "
        f"facts +{result.facts_new} ({result.facts_total} total)"
    )
    embeddings = (
        f"{result.chunk_embeddings} chunk(s), "
        f"{result.entity_embeddings} entit(y/ies)"
    )
    if result.embedding_dim is not None:
        embeddings += f", dim={result.embedding_dim}"
    typer.echo(f"Embeddings: {embeddings}")
    if result.knowledge_hash:
        typer.echo(f"Knowledge : {result.knowledge_hash[:16]}…")
        typer.echo(f"Snapshot  : {result.snapshot_id}")

    for warning in result.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW)
    for chunk_label, message in result.errors:
        typer.secho(
            f"rejected: {chunk_label}: {message}",
            err=True,
            fg=typer.colors.YELLOW,
        )


def _describe_drift(item) -> str:
    d = item.detail
    kind = item.drift_type
    if kind == "conflict":
        return (
            f"{d.get('subject')} / {d.get('predicate')}: "
            f"{d.get('old_value')!s} -> {d.get('new_value')!s} "
            f"({d.get('old_source')} -> {d.get('new_source')})"
        )
    if kind in ("possibly_stale", "confirmed_stale"):
        return (
            f"{d.get('subject')} / {d.get('predicate')} "
            f"[{d.get('reason')}] ({d.get('source')})"
        )
    if kind == "superseded":
        return f"{d.get('old_source')} -> {d.get('new_source')}"
    if kind == "duplicate":
        return (
            f"{d.get('entity_a')} ≡ {d.get('entity_b')}: "
            f"{d.get('predicate')}={d.get('object')!s} [{d.get('recall')}]"
        )
    return d.get("reason", "")


_SEVERITY_COLOR = {
    "critical": typer.colors.RED,
    "high": typer.colors.RED,
    "medium": typer.colors.YELLOW,
    "warning": typer.colors.BLUE,
}


def _print_detection_summary(result: DetectionResult) -> None:
    typer.echo(f"Drift Detection ({DETECTOR_VERSION})")
    typer.echo(
        f"Drift    : {result.new_count} new, {result.suppressed_count} already known"
    )
    counts = result.by_type
    typer.echo(
        "By type  : "
        f"conflict {counts['conflict']} | "
        f"possibly_stale {counts['possibly_stale']} | "
        f"confirmed_stale {counts['confirmed_stale']} | "
        f"superseded {counts['superseded']} | "
        f"duplicate {counts['duplicate']}"
    )
    for item in result.items:
        if not item.is_new:
            continue
        color = _SEVERITY_COLOR.get(item.severity, typer.colors.MAGENTA)
        typer.secho(
            f"  [{item.severity}] {item.drift_type}: {_describe_drift(item)}",
            fg=color,
        )


def _run_scan(
    path: Path,
) -> tuple[
    IngestionResult, ExtractionResult | None, DetectionResult | None
] | None:
    """Ingest, extract and detect within one transaction/workspace."""
    try:
        preparation = prepare_check(path)
        config = preparation.config
        if config.extraction is None:
            raise TruthLayerError(
                "scan requires an 'extraction' section in .truthlayer.yaml "
                "(provider/model/base_url); 'check' only ingests documents"
            )
        with SessionLocal() as session:
            from truthlayer.extraction.service import KnowledgeExtractionService
            from truthlayer.ingestion.service import DocumentIngestionService

            ingestion = DocumentIngestionService(
                session, config, preparation.config_path.parent
            ).run()

            llm = build_llm(config.extraction)
            embedder = build_embedder(config.embedding)
            extraction = KnowledgeExtractionService(
                session,
                config,
                preparation.config_path.parent,
                llm,
                embedder,
            ).run()

            scan_run = session.get(ScanRun, extraction.scan_run_id)
            detection = DriftDetectionService(session, config).run(
                extraction.workspace_id, scan_run
            )
            session.commit()
        return ingestion, extraction, detection
    except TruthLayerError as exc:
        typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
        return None
    except SQLAlchemyError as exc:
        typer.secho(
            f"database error: {type(exc).__name__}: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        return None


@app.command()
def scan(
    path: Path = typer.Argument(..., help="Workspace directory or config file."),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Ingest, extract facts/evidence and write an immutable snapshot."""
    _configure_logging(verbose)
    outcome = _run_scan(path)
    if outcome is None:
        raise typer.Exit(code=2)

    ingestion, extraction, detection = outcome
    _print_ingestion_summary(ingestion)
    if extraction is not None:
        _print_extraction_summary(extraction)
    if detection is not None:
        _print_detection_summary(detection)
    raise typer.Exit(code=2 if ingestion.failed else 0)


@drift_app.command("list")
def drift_list() -> None:
    """List drifts (Sprint 5)."""
    typer.echo("drift list is not implemented yet (Sprint 5)")


@drift_app.command("show")
def drift_show(drift_id: str = typer.Argument(...)) -> None:
    """Show one drift (Sprint 5)."""
    typer.echo(f"drift show {drift_id} is not implemented yet (Sprint 5)")


@drift_app.command("ignore")
def drift_ignore(drift_id: str = typer.Argument(...)) -> None:
    """Ignore one drift (Sprint 5)."""
    typer.echo(f"drift ignore {drift_id} is not implemented yet (Sprint 5)")


@app.command()
def resolve(
    drift_id: str = typer.Argument(...),
    decision: str = typer.Option(
        ..., "--decision", help="accept_newer|keep_old|manual_override|false_positive"
    ),
) -> None:
    """Resolve a drift (Sprint 5)."""
    typer.echo(
        f"resolve {drift_id} as {decision} is not implemented yet (Sprint 5)"
    )


if __name__ == "__main__":
    app()
