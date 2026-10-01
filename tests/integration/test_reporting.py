"""Integration: ReportBuilder assembly + fail_on evaluation on PG."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.integration import _kbseed as seed
from truthlayer.config import TruthLayerConfig
from truthlayer.reporting.builder import ReportBuilder
from truthlayer.reporting.html_report import render_html
from truthlayer.reporting.json_report import render_json
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


def _config(name: str, fail_on: str) -> TruthLayerConfig:
    return TruthLayerConfig.model_validate(
        {
            "workspace": {"name": name},
            "sources": [
                {"path": "./docs", "authority": 0.9, "type": "policy"}
            ],
            "ci": {"fail_on": fail_on},
        }
    )


@pytest.fixture
def world(session: Session):
    ws_name = f"rep-it-{uuid.uuid4().hex[:8]}"
    ws = seed.make_workspace(session, ws_name)
    run = seed.make_scan_run(session, ws)

    old_doc, old_chunk = seed.make_document(
        session, ws, "price_2025.csv", source_type="pricing"
    )
    new_doc, new_chunk = seed.make_document(
        session, ws, "price_2026.csv", source_type="pricing",
        version_label="2026",
    )
    entity = seed.make_entity(session, ws, "CRM Pro")
    fact_old = seed.make_fact(
        session, ws, entity, "标准月费", 149,
        document=old_doc, chunk=old_chunk,
        observed=datetime(2025, 4, 1, tzinfo=timezone.utc),
        valid_from=date(2025, 4, 1),
        quote="CRM Pro 标准月费 149 元/坐席/月",
        source_type="pricing",
    )
    fact_new = seed.make_fact(
        session, ws, entity, "标准月费", 199,
        document=new_doc, chunk=new_chunk,
        observed=datetime(2026, 4, 1, tzinfo=timezone.utc),
        valid_from=date(2026, 4, 1),
        quote="CRM Pro 标准月费 199 元/坐席/月",
        source_type="pricing",
    )
    conflict = seed.make_drift(
        session, run,
        drift_type="conflict", severity="high",
        target_id=fact_old.id,
        detail={
            "reason": "overlapping_validity_different_objects",
            "subject": "CRM Pro", "predicate": "标准月费",
            "old_value": 149, "new_value": 199,
            "old_source": "price_2025.csv", "new_source": "price_2026.csv",
        },
        old_fact=fact_old, new_fact=fact_new,
        old_document=old_doc, new_document=new_doc,
        subject=entity, predicate="标准月费",
    )
    superseded = seed.make_drift(
        session, run,
        drift_type="superseded", severity="medium",
        target_type="document", target_id=old_doc.id,
        detail={
            "reason": "explicit_version_replacement",
            "old_source": "price_2025.csv", "new_source": "price_2026.csv",
        },
        old_document=old_doc, new_document=new_doc,
    )
    session.flush()
    return {
        "name": ws_name, "ws": ws, "run": run,
        "entity": entity, "fact_old": fact_old, "fact_new": fact_new,
        "old_doc": old_doc, "new_doc": new_doc,
        "conflict": conflict, "superseded": superseded,
    }


def test_report_counts_evidence_and_sources(
    session: Session, world: dict
) -> None:
    report = ReportBuilder(session).build(
        world["ws"].id, _config(world["name"], "critical"),
        scan_run=world["run"],
    )

    s = report.summary
    assert s.workspace_name == world["name"]
    assert s.documents_total == 2
    assert s.entities_total == 1
    assert s.facts_total == 2
    assert s.open_total == 2
    assert s.open_by_type == {
        "conflict": 1, "possibly_stale": 0, "confirmed_stale": 0,
        "superseded": 1, "duplicate": 0,
    }

    issue = next(i for i in report.issues if i.drift_type == "conflict")
    assert issue.old_fact is not None and issue.new_fact is not None
    assert issue.old_fact.object_display == "149"
    assert issue.new_fact.object_display == "199"
    assert issue.old_source.filename == "price_2025.csv"
    assert issue.new_source.version_label == "2026"
    quotes = {ev.quote for ev in issue.evidence}
    assert "CRM Pro 标准月费 149 元/坐席/月" in quotes
    assert "CRM Pro 标准月费 199 元/坐席/月" in quotes
    assert issue.why and issue.recommendation
    assert "accept_newer" in issue.suggested_decisions

    docs_issue = next(i for i in report.issues if i.drift_type == "superseded")
    assert docs_issue.old_fact is None
    assert docs_issue.old_source.filename == "price_2025.csv"
    assert docs_issue.new_source.filename == "price_2026.csv"


def test_fail_on_controls_ci_badge(session: Session, world: dict) -> None:
    high = ReportBuilder(session).build(
        world["ws"].id, _config(world["name"], "high"),
        scan_run=world["run"],
    )
    assert high.summary.ci.triggered is True
    assert high.summary.ci.blocking_count == 1

    critical = ReportBuilder(session).build(
        world["ws"].id, _config(world["name"], "critical"),
        scan_run=world["run"],
    )
    assert critical.summary.ci.triggered is False
    assert critical.summary.ci.blocking_count == 0

    none = ReportBuilder(session).build(
        world["ws"].id, _config(world["name"], "none"),
        scan_run=world["run"],
    )
    assert none.summary.ci.triggered is False


def test_report_reflects_resolution_and_build_issue(
    session: Session, world: dict
) -> None:
    ResolutionService(session).resolve(
        world["conflict"].id,
        decision="accept_newer",
        resolved_by="carol",
        reason_code="newer_version",
        reason="2026 价格表生效",
    )
    session.flush()

    report = ReportBuilder(session).build(
        world["ws"].id, _config(world["name"], "high"),
        scan_run=world["run"],
    )
    assert report.summary.open_total == 1
    assert report.summary.resolved_total == 1
    assert [i.drift_type for i in report.issues] == ["superseded"]

    issue = ReportBuilder(session).build_issue(world["conflict"])
    assert issue.status == "resolved"
    assert issue.resolution is not None
    assert issue.resolution.decision == "accept_newer"
    assert issue.resolution.reason_code == "newer_version"
    assert issue.resolution.resolved_by == "carol"


def test_json_and_html_round_trip(session: Session, world: dict, tmp_path) -> None:
    report = ReportBuilder(session).build(
        world["ws"].id, _config(world["name"], "high"),
        scan_run=world["run"],
    )
    text = render_json(report)
    payload = json.loads(text)
    assert payload["summary"]["workspace_name"] == world["name"]
    assert render_json(report) == text  # deterministic

    json_path = tmp_path / "report.json"
    json_path.write_text(text, encoding="utf-8")
    assert json_path.exists()

    html = render_html(report)
    assert "TruthLayer 知识可靠性报告" in html
    assert "CRM Pro 标准月费 149 元" in html
    assert "检查未通过" in html
    (tmp_path / "report.html").write_text(html, encoding="utf-8")


def test_report_without_scan_run_uses_latest_snapshot_hash(
    session: Session, world: dict
) -> None:
    # No scan_run given (e.g. 'check --html' regeneration path): must still
    # build from current rows without crashing.
    report = ReportBuilder(session).build(
        world["ws"].id, _config(world["name"], "critical")
    )
    assert report.summary.scan_run_id is None
    assert report.summary.open_total == 2
