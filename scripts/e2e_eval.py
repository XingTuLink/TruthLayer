"""End-to-end HCR evaluation runner (opt-in; needs real Ollama + PostgreSQL).

Pipeline under test (the *whole* chain, including LLM extraction)::

    documents -> ingest -> Ollama extract (qwen2.5 + bge-m3)
              -> canonicalize -> deterministic detect -> report DTO

Unlike tests/unit/evaluation (zero-LLM golden gate), this exercises real
extraction, so it is non-deterministic and NOT a CI gate. Each run uses a
timestamped workspace name so cross-scan fingerprint suppression never hides
findings and the run is repeatable without touching existing demo data.

Outputs go to a run directory (default build/e2e_eval/<timestamp>), which is
gitignored: raw artifacts stay local, and only the human-labeled score record
is curated into examples/eval_corpus/results/.

Usage (PowerShell)::

    $env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://.../truthlayer"
    ./.venv/Scripts/python scripts/e2e_eval.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from truthlayer.db.orm import Entity, Fact, ScanRun  # noqa: E402
from truthlayer.db.session import SessionLocal  # noqa: E402
from truthlayer.detection.service import (  # noqa: E402
    DETECTOR_VERSION,
    DriftDetectionService,
)
from truthlayer.extraction.prompts import PROMPT_VERSION  # noqa: E402
from truthlayer.extraction.service import (  # noqa: E402
    KnowledgeExtractionService,
)
from truthlayer.ingestion.service import DocumentIngestionService  # noqa: E402
from truthlayer.providers.factory import build_embedder, build_llm  # noqa: E402
from truthlayer.reporting.builder import ReportBuilder  # noqa: E402
from truthlayer.reporting.json_report import render_json  # noqa: E402
from truthlayer.services.workspace import prepare_check  # noqa: E402

try:
    from importlib.metadata import version as _pkg_version

    TOOL_VERSION = _pkg_version("truthlayer")
except Exception:  # noqa: BLE001 - fallback for source checkouts
    TOOL_VERSION = "0.2.0"


def apply_llm_overrides(config):
    """Point extraction at an online model without editing the corpus YAML.

    Opt-in env overrides for the eval runner only:
    TRUTHLAYER_E2E_LLM_BASE_URL / TRUTHLAYER_E2E_LLM_MODEL. The API key is
    still read solely from TRUTHLAYER_LLM_API_KEY (or the slot's api_key_env)
    via the provider factory — never from CLI args or the config file.
    Embeddings are untouched (normally stay on local bge-m3).
    """
    base_url = os.environ.get("TRUTHLAYER_E2E_LLM_BASE_URL")
    model = os.environ.get("TRUTHLAYER_E2E_LLM_MODEL")
    if not (base_url or model) or config.extraction is None:
        return config, False
    updates = {}
    if base_url:
        updates["base_url"] = base_url
    if model:
        updates["model"] = model
    return (
        config.model_copy(
            update={"extraction": config.extraction.model_copy(update=updates)}
        ),
        True,
    )


def attribute_source_types_override():
    """Eval-only gray-release override for attribute resolution.

    TRUTHLAYER_E2E_ATTRIBUTE_SOURCE_TYPES="corporate,pricing" widens the
    phase-1 gray release (which defaults to source_type=pricing in the
    product) so corpora whose sources are typed generically can still
    exercise the audit channels. Product defaults and the CLI are
    untouched; the channels remain non-blocking.
    """
    raw = os.environ.get("TRUTHLAYER_E2E_ATTRIBUTE_SOURCE_TYPES")
    if not raw:
        return None
    types = frozenset(item.strip() for item in raw.split(",") if item.strip())
    return types or None


def _write_json(path: Path, payload, *, lines: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        if lines:
            for item in payload:
                handle.write(
                    json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                )
        else:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _compact_issue(idx: int, issue) -> dict:
    def _fact(fact):
        if fact is None:
            return None
        return {
            "subject": fact.subject,
            "predicate": fact.predicate,
            "object": fact.object_display,
            "valid_from": str(fact.valid_from) if fact.valid_from else None,
            "valid_to": str(fact.valid_to) if fact.valid_to else None,
            "observed_at": str(fact.observed_at) if fact.observed_at else None,
            "source": (
                fact.evidence[0].filename if fact.evidence else None
            ),
        }

    return {
        "idx": idx,
        "id": str(issue.id),
        "short_id": str(issue.id)[:8],
        "type": issue.drift_type,
        "severity": issue.severity,
        "subject": issue.subject,
        "predicate": issue.predicate,
        "title": issue.human_title or issue.title,
        "why": issue.why,
        "new_this_scan": issue.new_this_scan,
        "old_fact": _fact(issue.old_fact),
        "new_fact": _fact(issue.new_fact),
        "old_source": issue.old_source.filename if issue.old_source else None,
        "new_source": issue.new_source.filename if issue.new_source else None,
        "evidence": [
            {"filename": e.filename, "quote": e.quote} for e in issue.evidence
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "examples" / "eval_corpus",
        help="Workspace directory containing .truthlayer.yaml",
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "build" / "e2e_eval" / stamp,
        help="Output directory for run artifacts",
    )
    args = parser.parse_args()

    preparation = prepare_check(args.corpus)
    config = preparation.config
    if config.extraction is None:
        raise SystemExit("corpus config needs an 'extraction' section")
    config, llm_overridden = apply_llm_overrides(config)
    attr_source_types = attribute_source_types_override()

    run_name = f"{config.workspace.name}-{stamp}"
    config = config.model_copy(
        update={
            "workspace": config.workspace.model_copy(update={"name": run_name})
        }
    )

    print(f"[e2e] workspace={run_name}", flush=True)
    print(
        f"[e2e] llm={config.extraction.model} "
        f"base_url={config.extraction.base_url or 'api.openai.com'}"
        f"{' (env override)' if llm_overridden else ''} "
        f"embed={config.embedding.model if config.embedding else None}",
        flush=True,
    )
    if attr_source_types is not None:
        print(
            "[e2e] attribute gray-release source_types="
            f"{sorted(attr_source_types)} (eval override)",
            flush=True,
        )

    with SessionLocal() as session:
        ingestion = DocumentIngestionService(
            session, config, preparation.config_path.parent
        ).run()
        print(f"[e2e] ingested: parsed={ingestion.parsed} failed={ingestion.failed}", flush=True)

        llm = build_llm(config.extraction)
        embedder = build_embedder(config.embedding) if config.embedding else None
        extraction = KnowledgeExtractionService(
            session, config, preparation.config_path.parent, llm, embedder
        ).run()
        print(
            f"[e2e] extracted: entities={extraction.entities_total} "
            f"facts={extraction.facts_total} chunks={extraction.chunks_processed} "
            f"chunks_failed={extraction.chunks_failed}",
            flush=True,
        )
        if extraction.chunk_retries:
            print(
                f"[e2e] transient retries: {extraction.chunk_retries} "
                f"(chunks recovered: {extraction.chunks_recovered})",
                flush=True,
            )
        for label, message in extraction.errors:
            print(f"[e2e]   ! extraction error {label}: {message[:160]}", flush=True)

        scan_run = session.get(ScanRun, extraction.scan_run_id)
        detection = DriftDetectionService(session, config).run(
            extraction.workspace_id,
            scan_run,
            llm=llm,
            attribute_source_types=attr_source_types,
        )
        report = ReportBuilder(session).build(
            extraction.workspace_id, config, scan_run=scan_run, detection=detection
        )

        # Fact dump (for analyzing extraction yield vs planted ground truth).
        entities = {
            e.id: e.canonical_name
            for e in session.scalars(
                select(Entity).where(Entity.workspace_id == extraction.workspace_id)
            ).all()
        }
        facts = session.scalars(
            select(Fact).where(Fact.workspace_id == extraction.workspace_id)
        ).all()
        fact_rows = [
            {
                "subject": entities.get(f.subject_entity_id, str(f.subject_entity_id)),
                "predicate": f.predicate,
                "object": (
                    entities.get(f.object_entity_id)
                    if f.object_entity_id is not None
                    else f.object_value
                ),
                "object_type": f.object_type or "entity",
                "unit": f.measure_unit,
                "currency": f.currency,
                "tax_basis": f.tax_basis,
                "valid_from": str(f.valid_from) if f.valid_from else None,
                "valid_to": str(f.valid_to) if f.valid_to else None,
                "observed_at": (
                    f.observed_at.date().isoformat() if f.observed_at else None
                ),
                "status": f.status,
                "confidence": round(f.confidence, 3),
                "quote": (
                    f.evidence_jsonb[0].get("quote") if f.evidence_jsonb else None
                ),
            }
            for f in facts
        ]

        session.commit()

    args.out.mkdir(parents=True, exist_ok=True)
    try:
        corpus_label = str(args.corpus.resolve().relative_to(ROOT))
    except ValueError:
        corpus_label = str(args.corpus)
    _write_json(args.out / "report.json", json.loads(render_json(report)))
    _write_json(
        args.out / "drifts.jsonl",
        [_compact_issue(i, issue) for i, issue in enumerate(report.issues)],
        lines=True,
    )
    _write_json(args.out / "facts.jsonl", fact_rows, lines=True)
    if report.summary.attribute_review:
        review = report.summary.attribute_review
        review_rows = [
            item.model_dump()
            for channel in (
                review.pending_review,
                review.cross_attribute_review,
                review.normalized_equivalent,
            )
            for item in channel
        ]
        _write_json(args.out / "review_channels.jsonl", review_rows, lines=True)
    _write_json(
        args.out / "manifest.json",
        {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "workspace": run_name,
            "corpus": corpus_label,
            "llm_model": config.extraction.model,
            "embedder_model": config.embedding.model if config.embedding else None,
            "prompt_version": PROMPT_VERSION,
            "tool_version": TOOL_VERSION,
            "detector_version": DETECTOR_VERSION,
            "documents_parsed": ingestion.parsed,
            "documents_failed": ingestion.failed,
            "chunks_processed": extraction.chunks_processed,
            "chunks_failed": extraction.chunks_failed,
            "chunk_retries": extraction.chunk_retries,
            "chunks_recovered": extraction.chunks_recovered,
            "extraction_errors": [
                {"chunk": label, "error": message[:300]}
                for label, message in extraction.errors
            ],
            "entities_total": extraction.entities_total,
            "facts_total": extraction.facts_total,
            "drifts_reported": len(report.issues),
            "new_this_scan": report.summary.new_this_scan,
            "open_by_type": report.summary.open_by_type,
            "by_severity": report.summary.ci.by_severity,
            "attribute_prompt_version": detection.attribute_prompt_version,
            "attribute_source_types": (
                sorted(attr_source_types)
                if attr_source_types is not None
                else ["pricing"]
            ),
            "review_channels": detection.channel_counts(),
            "review_notes": detection.attribute_notes,
            "knowledge_hash": report.summary.knowledge_hash,
            "notes": "Non-deterministic real-LLM run; not a CI gate.",
        },
    )

    print(f"[e2e] drifts reported: {len(report.issues)}", flush=True)
    print(f"[e2e] artifacts -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
