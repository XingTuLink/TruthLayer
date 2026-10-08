"""Typer CLI — thin adapter only (#31, #50, #67).

Exit codes follow #26: 0 pass, 1 fail_on threshold, 2 system/config/runtime.
"""

from __future__ import annotations

import getpass
import logging
import uuid
from pathlib import Path

import typer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from truthlayer import __version__
from truthlayer.db.orm import ScanRun, Workspace
from truthlayer.db.session import SessionLocal
from truthlayer.detection.service import (
    DETECTOR_VERSION,
    DetectionResult,
    DriftDetectionService,
)
from truthlayer.domain.enums import DriftStatus, DriftType
from truthlayer.domain.errors import TruthLayerError
from truthlayer.evaluation.harness import run_suite as run_qa_suite
from truthlayer.evaluation.loader import EvaluationError, load_cases
from truthlayer.evaluation.metrics import compute_metrics
from truthlayer.evaluation.report import (
    render_json as render_eval_json,
    render_markdown as render_eval_markdown,
    render_text as render_eval_text,
)
from truthlayer.extraction.service import ExtractionResult
from truthlayer.ingestion.service import IngestionResult
from truthlayer.providers.factory import build_embedder, build_llm
from truthlayer.reporting.builder import ReportBuilder
from truthlayer.reporting.dto import IssueReport, ReportDTO
from truthlayer.reporting.html_report import render_html
from truthlayer.reporting.humanize import short_title
from truthlayer.reporting.json_report import render_json
from truthlayer.resolution.service import ResolutionService
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
            "tip: 'truthlayer scan' extracts facts, detects drift and writes a "
            "snapshot; 'truthlayer drift list' reviews open findings",
            fg=typer.colors.BLUE,
        )


@app.command()
def check(
    path: Path = typer.Argument(
        ..., exists=False, help="Workspace directory or .truthlayer.yaml file."
    ),
    html: Path | None = typer.Option(
        None, "--html", help="Write a self-contained HTML report to this path."
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Write a deterministic JSON report to this path."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose logging."),
) -> None:
    """Ingest and check a knowledge workspace (#31).

    --html/--output render the latest persisted drift state for the workspace
    without spending LLM tokens; run 'truthlayer scan' first to populate it.
    """
    _configure_logging(verbose)
    result = _run_ingestion(path)
    if result is None:
        raise typer.Exit(code=2)

    _print_ingestion_summary(result)

    report_ok = True
    if html is not None or output is not None:
        report_ok = _write_latest_report(path, html, output)

    # Parser failures are runtime errors (#26, #34).
    raise typer.Exit(code=2 if result.failed or not report_ok else 0)


def _write_eval_artifact(path: Path, content: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    except OSError as exc:
        typer.secho(
            f"failed to write {path}: {exc}", err=True, fg=typer.colors.RED
        )
        return False


@app.command(name="eval")
def evaluate(
    cases: Path = typer.Option(
        Path("examples/qa_cases"),
        "--cases",
        help="Directory of golden QA case YAML files.",
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Write metrics as deterministic JSON."
    ),
    markdown: Path | None = typer.Option(
        None, "--markdown", help="Write a Markdown evaluation report."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose logging."),
) -> None:
    """Run the golden QA set against the deterministic detectors (#40, #42).

    No database and no LLM are involved: each case is mapped straight onto a
    KnowledgeState. Exit code 0 = Phase 0 Gate passed, 1 = Gate failed,
    2 = cases could not be loaded / artifact could not be written.
    """
    _configure_logging(verbose)
    try:
        qa_cases = load_cases(cases)
        suite = run_qa_suite(qa_cases)
        metrics = compute_metrics(suite, qa_cases)
    except EvaluationError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2)

    typer.echo(render_eval_text(metrics, suite, qa_cases))

    artifacts_ok = True
    if output is not None:
        artifacts_ok &= _write_eval_artifact(
            output, render_eval_json(metrics, suite, qa_cases)
        )
    if markdown is not None:
        artifacts_ok &= _write_eval_artifact(
            markdown, render_eval_markdown(metrics, suite, qa_cases)
        )
    if not artifacts_ok:
        raise typer.Exit(code=2)
    raise typer.Exit(code=0 if metrics.gate.passed else 1)


def _print_extraction_summary(result: ExtractionResult) -> None:
    typer.echo("Knowledge Extraction")
    typer.echo(f"Scan run  : {result.scan_run_id}")
    failed = f" | {result.chunks_failed} failed" if result.chunks_failed else ""
    measure = (
        f" | {result.facts_with_measure} with measure anchors"
        if result.facts_with_measure
        else ""
    )
    inferred = (
        result.valid_to_anchor_document_scope
        + result.valid_to_anchor_calendar_derived
    )
    anchors = (
        f" | valid_to anchors: {result.valid_to_anchor_quoted} quoted, "
        f"{inferred} review-only"
        if result.valid_to_anchor_quoted or inferred
        else ""
    )
    typer.echo(
        f"Chunks    : {result.chunks_processed} processed{failed} | "
        f"entities +{result.entities_new} ({result.entities_total} total) | "
        f"facts +{result.facts_new} ({result.facts_total} total){measure}{anchors}"
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
    if kind == "confirmed_stale" and d.get("reason") == (
        "document_self_declared_expired"
    ):
        return (
            f"DOCUMENT {d.get('old_source')} self-declared validity end "
            f"{d.get('valid_to')} already past (evidence anchor: "
            f"{d.get('valid_to_anchor')})"
        )
    if kind in ("possibly_stale", "confirmed_stale"):
        return (
            f"{d.get('subject')} / {d.get('predicate')} "
            f"[{d.get('reason')}] ({d.get('source')})"
        )
    if kind == "reused_stale_value":
        return (
            f"{d.get('subject')} / {d.get('predicate')}: "
            f"{d.get('reused_value')!s} reused from {d.get('old_source')} "
            f"(head {d.get('new_source')} = {d.get('head_value')!s}, "
            f"quoted by {d.get('source')})"
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
        f"reused_stale_value {counts['reused_stale_value']} | "
        f"superseded {counts['superseded']} | "
        f"duplicate {counts['duplicate']}"
    )
    if result.review_total:
        channels = result.channel_counts()
        typer.secho(
            "Attr review (non-blocking, model-assisted, not reproducible): "
            f"pending {channels['pending_review']} | "
            f"cross-attribute {channels['cross_attribute_review']} | "
            f"normalized-equivalent {channels['normalized_equivalent']}",
            fg=typer.colors.YELLOW,
        )
    for item in result.items:
        if not item.is_new:
            continue
        color = _SEVERITY_COLOR.get(item.severity, typer.colors.MAGENTA)
        typer.secho(
            f"  [{item.severity}] {item.drift_type}: {_describe_drift(item)}",
            fg=color,
        )


def _print_ci_banner(report: ReportDTO) -> None:
    ci = report.summary.ci
    if ci.fail_on == "none":
        typer.secho(
            "CI        : fail_on=none — threshold checking disabled",
            fg=typer.colors.BLUE,
        )
        return
    if ci.triggered:
        typer.secho(
            f"CI        : FAIL — {ci.blocking_count} open drift(s) at or above "
            f"fail_on={ci.fail_on} (exit code 1)",
            fg=typer.colors.RED,
            err=True,
        )
    else:
        typer.secho(
            f"CI        : pass — {ci.open_count} open, all below "
            f"fail_on={ci.fail_on}",
            fg=typer.colors.GREEN,
        )


def _write_report_files(
    report: ReportDTO,
    html_path: Path | None,
    json_path: Path | None,
) -> bool:
    """Render after commit. Returns False on filesystem errors (#34)."""
    try:
        if json_path is not None:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(render_json(report), encoding="utf-8")
            typer.secho(f"JSON report: {json_path}", fg=typer.colors.GREEN)
        if html_path is not None:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(render_html(report), encoding="utf-8")
            typer.secho(f"HTML report: {html_path}", fg=typer.colors.GREEN)
    except OSError as exc:
        typer.secho(
            f"report error: {type(exc).__name__}: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        return False
    return True


def _write_latest_report(
    path: Path,
    html_path: Path | None,
    json_path: Path | None,
) -> bool:
    """Render the latest persisted state without running extraction (#31)."""
    try:
        preparation = prepare_check(path)
        config = preparation.config
        with SessionLocal() as session:
            workspace = session.scalar(
                select(Workspace).where(
                    Workspace.name == config.workspace.name
                )
            )
            if workspace is None:
                typer.secho(
                    "note: no previous scan for this workspace; run "
                    "'truthlayer scan <path>' before generating a drift report",
                    fg=typer.colors.YELLOW,
                )
                return True
            scan_run = session.scalar(
                select(ScanRun)
                .where(ScanRun.workspace_id == workspace.id)
                .order_by(ScanRun.created_at.desc())
            )
            report = ReportBuilder(session).build(
                workspace.id, config, scan_run=scan_run
            )
        return _write_report_files(report, html_path, json_path)
    except TruthLayerError as exc:
        typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
        return False
    except SQLAlchemyError as exc:
        typer.secho(
            f"database error: {type(exc).__name__}: {exc}",
            err=True,
            fg=typer.colors.RED,
        )
        return False


def _run_scan(
    path: Path,
) -> tuple[
    IngestionResult,
    ExtractionResult | None,
    DetectionResult | None,
    ReportDTO | None,
] | None:
    """Ingest, extract, detect and assemble the report in one transaction."""
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
                extraction.workspace_id, scan_run, llm=llm
            )
            # DTO is assembled pre-commit; files are written afterwards.
            report = ReportBuilder(session).build(
                extraction.workspace_id,
                config,
                scan_run=scan_run,
                detection=detection,
            )
            session.commit()
        return ingestion, extraction, detection, report
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
    html: Path | None = typer.Option(
        None, "--html", help="Write a self-contained HTML report to this path."
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Write a deterministic JSON report to this path."
    ),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Ingest, extract facts/evidence, detect drift and write a snapshot."""
    _configure_logging(verbose)
    outcome = _run_scan(path)
    if outcome is None:
        raise typer.Exit(code=2)

    ingestion, extraction, detection, report = outcome
    _print_ingestion_summary(ingestion)
    if extraction is not None:
        _print_extraction_summary(extraction)
    if detection is not None:
        _print_detection_summary(detection)
    if report is not None:
        _print_ci_banner(report)
        report_ok = _write_report_files(report, html, output)
    else:
        report_ok = True

    if ingestion.failed or not report_ok:
        raise typer.Exit(code=2)
    if report is not None and report.summary.ci.triggered:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


# --- drift management --------------------------------------------------------

_STATUS_CHOICES = [s.value for s in DriftStatus] + ["all"]
_TYPE_CHOICES = [t.value for t in DriftType]


@drift_app.command("list")
def drift_list(
    status_filter: str = typer.Option(
        DriftStatus.OPEN.value,
        "--status",
        help=f"One of: {', '.join(_STATUS_CHOICES)}.",
    ),
    drift_type: str | None = typer.Option(
        None, "--type", help=f"One of: {', '.join(_TYPE_CHOICES)}."
    ),
    workspace: str | None = typer.Option(
        None, "--workspace", help="Filter by workspace name."
    ),
) -> None:
    """List drift findings (open by default)."""
    if status_filter not in _STATUS_CHOICES:
        typer.secho(
            f"error: --status must be one of: {', '.join(_STATUS_CHOICES)}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)
    if drift_type is not None and drift_type not in _TYPE_CHOICES:
        typer.secho(
            f"error: --type must be one of: {', '.join(_TYPE_CHOICES)}",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    with SessionLocal() as session:
        try:
            records = ResolutionService(session).list_drifts(
                workspace_name=workspace,
                status=status_filter,
                drift_type=drift_type,
            )
        except SQLAlchemyError as exc:
            typer.secho(
                f"database error: {type(exc).__name__}: {exc}",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2) from exc

    if not records:
        typer.echo("no drifts found.")
        raise typer.Exit(code=0)

    for record in records:
        d = record.drift
        detail = dict(d.detail_jsonb or {})
        subject = record.subject_name or detail.get("subject")
        if subject:
            detail.setdefault("subject", subject)
        color = _SEVERITY_COLOR.get(d.severity, typer.colors.MAGENTA)
        typer.secho(
            f"{str(d.id)[:8]}  [{d.severity:<8}] {d.type:<16} "
            f"{record.workspace_name}",
            fg=color,
        )
        typer.echo(f"          {short_title(d.type, detail)}")


def _print_issue(issue: IssueReport) -> None:
    color = _SEVERITY_COLOR.get(issue.severity, typer.colors.MAGENTA)
    typer.secho(
        f"[{issue.severity}] {issue.drift_type} — {issue.title}",
        fg=color,
    )
    typer.echo(f"  drift id   : {issue.id}")
    typer.echo(f"  status     : {issue.status}")
    typer.echo(
        f"  detector   : {issue.detector_type} · "
        f"confidence {issue.confidence} · AI impact {issue.ai_impact_level}"
    )
    typer.echo(f"  detected at: {issue.detected_at:%Y-%m-%d %H:%M UTC}")

    for label, fact in (("OLD", issue.old_fact), ("NEW", issue.new_fact)):
        if fact is None:
            continue
        typer.secho(f"  -- {label} fact --", fg=typer.colors.CYAN)
        typer.echo(
            f"    {fact.subject} / {fact.predicate} = {fact.object_display}"
            f"  ({fact.object_type}, {fact.status})"
        )
        typer.echo(
            f"    valid {fact.valid_from or '?'} ~ {fact.valid_to or 'now'} · "
            f"observed {fact.observed_at:%Y-%m-%d}"
            if fact.observed_at
            else f"    valid {fact.valid_from or '?'} ~ {fact.valid_to or 'now'}"
        )

    for label, source in (
        ("OLD", issue.old_source),
        ("NEW", issue.new_source),
    ):
        if source is None:
            continue
        typer.echo(
            f"  {label.lower()} source  : {source.filename} "
            f"({source.source_type}, authority {source.authority_score}"
            + (f", version {source.version_label}" if source.version_label else "")
            + ")"
        )

    if issue.evidence:
        typer.echo("  evidence:")
        for ev in issue.evidence:
            page = f", p.{ev.page}" if ev.page else ""
            typer.echo(f"    “{ev.quote}”")
            typer.secho(
                f"      — {ev.filename or 'unknown'}{page} ({ev.source_type})",
                fg=typer.colors.BRIGHT_BLACK,
            )

    typer.secho(f"  why        : {issue.why}", fg=typer.colors.YELLOW)
    typer.secho(
        f"  action     : {issue.recommendation}", fg=typer.colors.BLUE
    )
    typer.echo(
        "  commands   : truthlayer resolve "
        f"{issue.id} --decision <{'|'.join(issue.suggested_decisions) or 'decision'}> "
        "[--reason-code ... --reason ...]"
    )
    typer.echo(f"               truthlayer drift ignore {issue.id}")

    if issue.resolution is not None:
        r = issue.resolution
        typer.secho(
            f"  resolved   : {r.decision} by {r.resolved_by} at "
            f"{r.resolved_at:%Y-%m-%d %H:%M}"
            + (f" ({r.reason_code})" if r.reason_code else ""),
            fg=typer.colors.GREEN,
        )
        if r.reason:
            typer.echo(f"               reason: {r.reason}")


@drift_app.command("show")
def drift_show(
    drift_id: uuid.UUID = typer.Argument(..., help="Drift UUID."),
) -> None:
    """Show one drift with old/new facts, evidence and recommended actions."""
    with SessionLocal() as session:
        try:
            drift = ResolutionService(session).get_drift(drift_id)
            issue = ReportBuilder(session).build_issue(drift)
        except TruthLayerError as exc:
            typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=2) from exc
        except SQLAlchemyError as exc:
            typer.secho(
                f"database error: {type(exc).__name__}: {exc}",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2) from exc
    _print_issue(issue)


@drift_app.command("ignore")
def drift_ignore(
    drift_id: uuid.UUID = typer.Argument(...),
    reason: str | None = typer.Option(None, "--reason", help="Free-text note."),
) -> None:
    """Ignore one open drift (remembered; it will not re-fire)."""
    with SessionLocal() as session:
        try:
            drift = ResolutionService(session).ignore(drift_id, reason=reason)
            new_status = drift.status
            session.commit()
        except TruthLayerError as exc:
            typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=2) from exc
        except SQLAlchemyError as exc:
            typer.secho(
                f"database error: {type(exc).__name__}: {exc}",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2) from exc
    typer.secho(
        f"drift {drift_id} is now {new_status}.", fg=typer.colors.GREEN
    )


@app.command()
def resolve(
    drift_id: uuid.UUID = typer.Argument(..., help="Drift UUID."),
    decision: str = typer.Option(
        ...,
        "--decision",
        help=(
            "accept_newer|keep_old|manual_override|false_positive"
        ),
    ),
    reason_code: str | None = typer.Option(
        None,
        "--reason-code",
        help="Controlled code, e.g. newer_version, still_valid, multi_valued.",
    ),
    reason: str | None = typer.Option(
        None, "--reason", help="Free-text explanation recorded for analysis."
    ),
    authority_fact_id: uuid.UUID | None = typer.Option(
        None,
        "--authority-fact-id",
        help="Override the winning fact (defaults: newer for accept_newer).",
    ),
    resolved_by: str = typer.Option(
        ...,
        "--by",
        default_factory=getpass.getuser,
        help="Operator identity (defaults to the OS user).",
    ),
) -> None:
    """Record a human decision on one open drift (#27)."""
    with SessionLocal() as session:
        try:
            record = ResolutionService(session).resolve(
                drift_id,
                decision=decision,
                resolved_by=resolved_by,
                reason=reason,
                reason_code=reason_code,
                authority_fact_id=authority_fact_id,
            )
            decided_by = record.resolved_by
            decision_value = record.decision
            session.commit()
        except TruthLayerError as exc:
            typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=2) from exc
        except SQLAlchemyError as exc:
            typer.secho(
                f"database error: {type(exc).__name__}: {exc}",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2) from exc
    typer.secho(
        f"drift {drift_id} resolved as {decision_value} by "
        f"{decided_by} (scope=single).",
        fg=typer.colors.GREEN,
    )


if __name__ == "__main__":
    app()
