"""Integration: four detectors + persistence + cross-scan dedupe on PG.

Constructs a small knowledge state directly via ORM rows (no LLM) covering
all five drift types, then runs DriftDetectionService twice:

* scan 1: 5 new drifts (one per type), ScanRun counters/version recorded
* scan 2: 0 new, 5 suppressed — fingerprints remember prior findings
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from truthlayer.config import TruthLayerConfig
from truthlayer.db.orm import (
    Chunk,
    Document,
    DocumentGroup,
    Drift,
    Entity,
    Fact,
    ScanRun,
    Workspace,
)
from truthlayer.detection.service import (
    DETECTOR_VERSION,
    DriftDetectionService,
)
from truthlayer.domain.evidence import Evidence

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent
while not (PROJECT_ROOT / "alembic.ini").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent

AS_OF = date(2026, 9, 25)


@pytest.fixture
def session(database_url: str):
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    engine = create_engine(database_url)
    try:
        with Session(engine) as db_session:
            yield db_session
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")


def _config() -> TruthLayerConfig:
    return TruthLayerConfig.model_validate(
        {
            "workspace": {"name": f"det-it-{uuid.uuid4().hex[:8]}"},
            "sources": [
                {"path": "./docs", "authority": 0.9, "type": "policy"},
            ],
            "rules": {
                "stale_after_days": 365,
                "pricing_stale_days": 90,
            },
            "severity": {
                "pricing_change": "high",
                "policy_change": "critical",
            },
        }
    )


def _seed_world(session: Session, config: TruthLayerConfig):
    ws = Workspace(name=config.workspace.name)
    session.add(ws)
    session.flush()

    group = DocumentGroup(workspace_id=ws.id, name="product_pricing")
    session.add(group)
    session.flush()

    def doc(name, source_type, group_id=None, previous_id=None, parsed_day=None):
        document = Document(
            workspace_id=ws.id,
            document_group_id=group_id,
            previous_version_id=previous_id,
            version_label=name,
            filename=name,
            file_hash=f"hash-{name}",
            content_hash=f"content-{name}",
            parsed_at=datetime(parsed_day.year, parsed_day.month, parsed_day.day,
                               tzinfo=timezone.utc),
            status="parsed",
            authority_score=0.9,
            metadata_jsonb={"relative_path": name, "source_type": source_type},
        )
        session.add(document)
        session.flush()
        chunk = Chunk(
            document_id=document.id,
            chunk_index=0,
            text=f"{name} 原文片段",
            token_count=10,
        )
        session.add(chunk)
        session.flush()
        return document, chunk

    p2025, c2025 = doc(
        "price_2025.csv", "pricing", group.id,
        parsed_day=date(2025, 4, 1),
    )
    p2026, c2026 = doc(
        "price_2026.csv", "pricing", group.id,
        previous_id=p2025.id, parsed_day=date(2026, 4, 1),
    )
    brochure, cb = doc(
        "brochure.md", "marketing", parsed_day=date(2026, 2, 1)
    )
    promo, cp = doc(
        "promo.csv", "pricing", parsed_day=date(2026, 3, 1)
    )
    org1, co1 = doc(
        "org1.txt", "org", parsed_day=date(2026, 1, 15)
    )
    org2, co2 = doc(
        "org2.txt", "org", parsed_day=date(2026, 6, 15)
    )

    def entity(name, etype="product"):
        e = Entity(
            workspace_id=ws.id,
            canonical_name=name,
            entity_type=etype,
        )
        session.add(e)
        session.flush()
        return e

    crm = entity("ACME CRM")
    crm_pro = entity("ACME CRM Pro")
    staff = entity("ACME 员工", etype="person")

    def fact(subject, predicate, *, value, object_type, chunk, document,
             observed=None):
        evidence = Evidence(
            document_id=document.id,
            chunk_id=chunk.id,
            quote=chunk.text,
            source_type=document.metadata_jsonb["source_type"],
            authority_score=0.9,
        )
        stored = (
            value.isoformat() if object_type == "date" and value else value
        )
        f = Fact(
            workspace_id=ws.id,
            subject_entity_id=subject.id,
            predicate=predicate,
            object_value=None if object_type == "entity" else stored,
            object_type=None if object_type == "entity" else object_type,
            object_entity_id=None,
            observed_at=(
                datetime(observed.year, observed.month, observed.day,
                         tzinfo=timezone.utc)
                if observed
                else None
            ),
            confidence=0.8,
            status="active",
            source_chunk_id=chunk.id,
            evidence_jsonb=[evidence.model_dump(mode="json")],
        )
        session.add(f)
        session.flush()
        return f

    # 2025 price (source explicitly superseded later) -> confirmed stale;
    # same value re-stated for an unmerged entity name -> duplicate.
    f1 = fact(crm, "list_price", value=149, object_type="number",
              chunk=c2025, document=p2025, observed=date(2025, 4, 1))
    fact(crm_pro, "currency", value="CNY", object_type="string",
         chunk=c2026, document=p2026, observed=date(2026, 9, 1))
    fact(crm_pro, "list_price", value=149, object_type="number",
         chunk=cb, document=brochure, observed=date(2026, 2, 1))
    # Old pricing fact with no replacement, past 90-day threshold.
    fact(crm_pro, "promo_price", value=99, object_type="number",
         chunk=cp, document=promo, observed=date(2026, 3, 1))
    # Cross-document contradiction, no supersession link -> conflict.
    fact(staff, "seat_zone", value="A区", object_type="string",
         chunk=co1, document=org1, observed=date(2026, 1, 15))
    fact(staff, "seat_zone", value="B区", object_type="string",
         chunk=co2, document=org2, observed=date(2026, 6, 15))

    session.flush()
    return ws, {"f1": f1, "p2025": p2025, "p2026": p2026, "crm": crm}


def _open_scan_run(session: Session, ws: Workspace) -> ScanRun:
    run = ScanRun(
        workspace_id=ws.id,
        config_hash="a" * 64,
        detector_version=None,
        prompt_version=None,
        started_at=datetime.now(timezone.utc),
        status="running",
    )
    session.add(run)
    session.flush()
    return run


def test_all_five_drift_types_detected_and_persisted(session: Session) -> None:
    config = _config()
    ws, refs = _seed_world(session, config)
    scan_run = _open_scan_run(session, ws)

    result = DriftDetectionService(
        session, config, as_of=AS_OF
    ).run(ws.id, scan_run)

    assert result.new_count == 5
    assert result.suppressed_count == 0
    assert result.by_type == {
        "conflict": 1,
        "possibly_stale": 1,
        "confirmed_stale": 1,
        "superseded": 1,
        "duplicate": 1,
    }

    # ScanRun reproducibility fields (#15).
    assert scan_run.detector_version == DETECTOR_VERSION
    assert scan_run.drift_count == 5

    db_drifts = session.scalars(select(Drift)).all()
    assert len(db_drifts) == 5
    by_type = {d.type: d for d in db_drifts}

    conflict = by_type["conflict"]
    assert conflict.target_type == "fact"
    assert conflict.severity == "high"
    assert conflict.ai_impact_level == "high"
    assert conflict.predicate == "seat_zone"
    assert conflict.old_document_id is not None
    assert conflict.new_document_id is not None

    confirmed = by_type["confirmed_stale"]
    assert confirmed.target_type == "fact"
    assert confirmed.old_fact_id == refs["f1"].id
    assert confirmed.new_document_id == refs["p2026"].id
    assert confirmed.detail_jsonb["reason"] == "superseding_source"

    possible = by_type["possibly_stale"]
    assert possible.severity == "warning"
    assert possible.detail_jsonb["threshold_days"] == 90

    superseded = by_type["superseded"]
    assert superseded.target_type == "document"
    assert superseded.target_id == refs["p2025"].id
    assert superseded.old_document_id == refs["p2025"].id
    assert superseded.new_document_id == refs["p2026"].id

    duplicate = by_type["duplicate"]
    assert duplicate.subject_entity_id is not None
    assert duplicate.detail_jsonb["reason"] == "unmerged_entities_same_fact"

    # Fingerprints are stored for future dedupe.
    assert all(d.detail_jsonb.get("fingerprint") for d in db_drifts)


def test_second_scan_suppresses_known_findings(session: Session) -> None:
    config = _config()
    ws, _ = _seed_world(session, config)

    first_run = _open_scan_run(session, ws)
    first = DriftDetectionService(session, config, as_of=AS_OF).run(
        ws.id, first_run
    )
    assert first.new_count == 5

    second_run = _open_scan_run(session, ws)
    second = DriftDetectionService(session, config, as_of=AS_OF).run(
        ws.id, second_run
    )

    assert second.new_count == 0
    assert second.suppressed_count == 5
    assert second_run.detector_version == DETECTOR_VERSION
    assert second_run.drift_count == 0

    # No duplicate Drift rows — findings are remembered, not re-reported.
    total = session.scalar(select(func.count()).select_from(Drift))
    assert total == 5
    run_counts = session.execute(
        select(ScanRun.id, ScanRun.drift_count).order_by(ScanRun.created_at)
    ).all()
    assert [row[1] for row in run_counts] == [5, 0]
