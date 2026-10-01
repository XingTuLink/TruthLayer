"""Integration: Resolution lifecycle on PostgreSQL (#27)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.integration import _kbseed as seed
from truthlayer.db.orm import Drift, Resolution
from truthlayer.domain.enums import DriftStatus
from truthlayer.domain.errors import DomainValidationError, UserInputError
from truthlayer.resolution.service import ResolutionService

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent
while not (PROJECT_ROOT / "alembic.ini").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent


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


@pytest.fixture
def world(session: Session):
    ws = seed.make_workspace(session, f"res-it-{uuid.uuid4().hex[:8]}")
    run = seed.make_scan_run(session, ws)
    doc_old, chunk_old = seed.make_document(
        session, ws, "policy_2025.md", source_type="policy"
    )
    doc_new, chunk_new = seed.make_document(
        session, ws, "policy_2026.md", source_type="policy", authority=0.95
    )
    entity = seed.make_entity(session, ws, "差旅制度")
    observed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fact_old = seed.make_fact(
        session, ws, entity, "住宿标准", 300,
        document=doc_old, chunk=chunk_old,
        observed=observed, valid_from=date(2025, 1, 1),
        quote="住宿标准每晚 300 元",
    )
    fact_new = seed.make_fact(
        session, ws, entity, "住宿标准", 400,
        document=doc_new, chunk=chunk_new,
        observed=datetime(2026, 6, 1, tzinfo=timezone.utc),
        valid_from=date(2026, 6, 1),
        quote="住宿标准每晚 400 元",
    )
    conflict = seed.make_drift(
        session, run,
        drift_type="conflict", severity="high",
        target_id=fact_old.id,
        detail={
            "reason": "overlapping_validity_different_objects",
            "subject": "差旅制度", "predicate": "住宿标准",
            "old_value": 300, "new_value": 400,
            "old_source": "policy_2025.md", "new_source": "policy_2026.md",
        },
        old_fact=fact_old, new_fact=fact_new,
        old_document=doc_old, new_document=doc_new,
        subject=entity, predicate="住宿标准",
    )
    superseded = seed.make_drift(
        session, run,
        drift_type="superseded", severity="medium",
        target_type="document", target_id=doc_old.id,
        detail={
            "reason": "explicit_version_replacement",
            "old_source": "policy_2025.md", "new_source": "policy_2026.md",
        },
        old_document=doc_old, new_document=doc_new,
    )
    session.flush()
    return {
        "ws": ws, "run": run, "entity": entity,
        "fact_old": fact_old, "fact_new": fact_new,
        "doc_old": doc_old, "doc_new": doc_new,
        "conflict": conflict, "superseded": superseded,
    }


def test_resolve_accept_newer_persists_single_resolution(
    session: Session, world: dict
) -> None:
    svc = ResolutionService(session)
    record = svc.resolve(
        world["conflict"].id,
        decision="accept_newer",
        resolved_by="alice",
        reason_code="source_updated",
        reason="新政策已发布",
    )
    session.flush()

    assert record.scope == "single"
    assert record.authority_fact_id == world["fact_new"].id
    assert record.pattern_jsonb is None

    stored = session.get(Drift, world["conflict"].id)
    assert stored.status == DriftStatus.RESOLVED.value
    rows = session.scalars(
        select(Resolution).where(Resolution.drift_id == stored.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].resolved_by == "alice"
    assert rows[0].reason_code == "source_updated"


def test_resolve_is_one_shot(session: Session, world: dict) -> None:
    svc = ResolutionService(session)
    svc.resolve(
        world["conflict"].id, decision="keep_old", resolved_by="alice"
    )
    session.flush()
    with pytest.raises(DomainValidationError, match="already resolved"):
        svc.resolve(
            world["conflict"].id, decision="accept_newer", resolved_by="bob"
        )


def test_invalid_decision_and_reason_code(
    session: Session, world: dict
) -> None:
    svc = ResolutionService(session)
    with pytest.raises(UserInputError, match="invalid decision"):
        svc.resolve(
            world["conflict"].id, decision="accept_both", resolved_by="alice"
        )
    with pytest.raises(UserInputError, match="invalid reason_code"):
        svc.resolve(
            world["conflict"].id,
            decision="accept_newer",
            resolved_by="alice",
            reason_code="made_up",
        )


def test_keep_old_authority_and_false_positive(
    session: Session, world: dict
) -> None:
    svc = ResolutionService(session)
    kept = svc.resolve(
        world["conflict"].id, decision="keep_old", resolved_by="alice"
    )
    assert kept.authority_fact_id == world["fact_old"].id

    fp = svc.resolve(
        world["superseded"].id,
        decision="false_positive",
        resolved_by="alice",
        reason_code="detector_noise",
    )
    assert fp.authority_fact_id is None


def test_explicit_authority_must_belong_to_drift(
    session: Session, world: dict
) -> None:
    svc = ResolutionService(session)
    with pytest.raises(DomainValidationError, match="old/new facts"):
        svc.resolve(
            world["conflict"].id,
            decision="manual_override",
            resolved_by="alice",
            authority_fact_id=uuid.uuid4(),
        )


def test_ignore_lifecycle(session: Session, world: dict) -> None:
    svc = ResolutionService(session)
    drift = svc.ignore(world["conflict"].id, reason="稍后复核")
    assert drift.status == DriftStatus.IGNORED.value
    assert drift.detail_jsonb["ignore_reason"] == "稍后复核"
    session.flush()

    # Idempotent.
    again = svc.ignore(world["conflict"].id)
    assert again.status == DriftStatus.IGNORED.value

    # Resolved drifts cannot be ignored.
    svc.resolve(
        world["superseded"].id, decision="accept_newer", resolved_by="alice"
    )
    session.flush()
    with pytest.raises(DomainValidationError, match="resolved"):
        svc.ignore(world["superseded"].id)

    # Ignored drifts can still be shown but not re-resolved.
    with pytest.raises(DomainValidationError, match="already ignored"):
        svc.resolve(
            world["conflict"].id, decision="keep_old", resolved_by="bob"
        )


def test_get_drift_unknown_raises(session: Session) -> None:
    with pytest.raises(UserInputError, match="not found"):
        ResolutionService(session).get_drift(uuid.uuid4())


def test_list_drifts_filters_and_orders(
    session: Session, world: dict
) -> None:
    svc = ResolutionService(session)
    svc.ignore(world["superseded"].id)
    session.flush()

    open_records = svc.list_drifts(
        workspace_name=world["ws"].name, status="open"
    )
    assert [r.drift.id for r in open_records] == [world["conflict"].id]

    all_records = svc.list_drifts(
        workspace_name=world["ws"].name, status="all"
    )
    assert len(all_records) == 2

    ignored = svc.list_drifts(status="ignored")
    assert [r.drift.id for r in ignored] == [world["superseded"].id]

    typed = svc.list_drifts(status="all", drift_type="conflict")
    assert len(typed) == 1
    assert typed[0].workspace_name == world["ws"].name
    assert typed[0].subject_name == "差旅制度"

    assert svc.list_drifts(workspace_name="no-such-workspace") == []
