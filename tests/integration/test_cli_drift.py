"""Integration: drift/resolve CLI commands against PostgreSQL.

The CLI is a thin adapter: SessionLocal is rebound to a throwaway database;
business behavior lives in the services (tested directly elsewhere).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from tests.integration import _kbseed as seed
from truthlayer.cli import app as cli_module
from truthlayer.cli.app import app
from truthlayer.db.orm import Drift
from truthlayer.domain.enums import DriftStatus

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent
while not (PROJECT_ROOT / "alembic.ini").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent

runner = CliRunner()


@pytest.fixture
def cli_env(database_url: str, monkeypatch):
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(cli_module, "SessionLocal", factory)

    with factory() as session:
        ws = seed.make_workspace(session, f"cli-it-{uuid.uuid4().hex[:8]}")
        run = seed.make_scan_run(session, ws)
        old_doc, old_chunk = seed.make_document(
            session, ws, "policy_2025.md"
        )
        new_doc, new_chunk = seed.make_document(
            session, ws, "policy_2026.md"
        )
        entity = seed.make_entity(session, ws, "差旅制度")
        fact_old = seed.make_fact(
            session, ws, entity, "住宿标准", 300,
            document=old_doc, chunk=old_chunk,
            observed=datetime(2025, 1, 1, tzinfo=timezone.utc),
            valid_from=date(2025, 1, 1),
            quote="住宿标准每晚 300 元",
        )
        fact_new = seed.make_fact(
            session, ws, entity, "住宿标准", 400,
            document=new_doc, chunk=new_chunk,
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
                "old_source": "policy_2025.md",
                "new_source": "policy_2026.md",
            },
            old_fact=fact_old, new_fact=fact_new,
            old_document=old_doc, new_document=new_doc,
            subject=entity, predicate="住宿标准",
        )
        superseded = seed.make_drift(
            session, run,
            drift_type="superseded", severity="medium",
            target_type="document", target_id=old_doc.id,
            detail={
                "reason": "explicit_version_replacement",
                "old_source": "policy_2025.md", "new_source": "policy_2026.md",
            },
            old_document=old_doc, new_document=new_doc,
        )
        session.commit()
        ids = {
            "workspace": ws.name,
            "conflict": conflict.id,
            "superseded": superseded.id,
        }

    yield ids

    engine.dispose()
    command.downgrade(cfg, "base")


def test_drift_list_filters(cli_env: dict) -> None:
    result = runner.invoke(app, ["drift", "list"])
    assert result.exit_code == 0, result.output
    assert "差旅制度" in result.output
    assert "住宿标准" in result.output
    # Default view hides nothing between the two open rows.
    assert "conflict" in result.output
    assert "superseded" in result.output

    only_conflict = runner.invoke(
        app, ["drift", "list", "--type", "conflict"]
    )
    assert "superseded" not in only_conflict.output

    assert (
        runner.invoke(
            app, ["drift", "list", "--status", "bogus"]
        ).exit_code
        == 2
    )

    empty = runner.invoke(
        app, ["drift", "list", "--workspace", "no-such-workspace"]
    )
    assert empty.exit_code == 0
    assert "no drifts found" in empty.output


def test_drift_show_prints_evidence_and_actions(cli_env: dict) -> None:
    result = runner.invoke(app, ["drift", "show", str(cli_env["conflict"])])
    assert result.exit_code == 0, result.output
    assert "住宿标准每晚 300 元" in result.output
    assert "住宿标准每晚 400 元" in result.output
    assert "policy_2025.md" in result.output
    assert "truthlayer resolve" in result.output

    assert (
        runner.invoke(app, ["drift", "show", str(uuid.uuid4())]).exit_code == 2
    )
    assert runner.invoke(app, ["drift", "show", "not-a-uuid"]).exit_code == 2


def test_ignore_hides_from_default_list(cli_env: dict) -> None:
    result = runner.invoke(
        app, ["drift", "ignore", str(cli_env["superseded"]), "--reason", "先放着"]
    )
    assert result.exit_code == 0, result.output
    assert "ignored" in result.output

    listing = runner.invoke(app, ["drift", "list"])
    assert "superseded" not in listing.output

    ignored = runner.invoke(app, ["drift", "list", "--status", "ignored"])
    assert str(cli_env["superseded"])[:8] in ignored.output

    # Resolved drifts cannot be ignored.
    runner.invoke(
        app,
        [
            "resolve", str(cli_env["conflict"]),
            "--decision", "accept_newer",
        ],
    )
    rejected = runner.invoke(
        app, ["drift", "ignore", str(cli_env["conflict"])]
    )
    assert rejected.exit_code == 2


def test_resolve_happy_path_and_guardrails(cli_env: dict) -> None:
    bad = runner.invoke(
        app,
        ["resolve", str(cli_env["conflict"]), "--decision", "accept_both"],
    )
    assert bad.exit_code == 2
    assert "invalid decision" in bad.output

    ok = runner.invoke(
        app,
        [
            "resolve", str(cli_env["conflict"]),
            "--decision", "accept_newer",
            "--reason-code", "source_updated",
            "--reason", "新制度生效",
            "--by", "alice",
        ],
    )
    assert ok.exit_code == 0, ok.output
    assert "accept_newer" in ok.output and "alice" in ok.output

    shown = runner.invoke(app, ["drift", "show", str(cli_env["conflict"])])
    assert "resolved" in shown.output
    assert "source_updated" in shown.output

    # One resolution per drift.
    again = runner.invoke(
        app,
        ["resolve", str(cli_env["conflict"]), "--decision", "keep_old"],
    )
    assert again.exit_code == 2
    assert "already resolved" in again.output


def test_foreign_authority_fact_exits_2(cli_env: dict) -> None:
    result = runner.invoke(
        app,
        [
            "resolve", str(cli_env["conflict"]),
            "--decision", "manual_override",
            "--authority-fact-id", str(uuid.uuid4()),
        ],
    )
    assert result.exit_code == 2
    assert "old/new facts" in result.output
