"""Integration: Alembic migration applies cleanly on PostgreSQL.

Skipped unless TRUTHLAYER_DATABASE_URL points at a running PostgreSQL 15+
with the pgvector extension available.

Run with:
    TRUTHLAYER_DATABASE_URL=postgresql+psycopg://... pytest -m integration
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent
while not (PROJECT_ROOT / "alembic.ini").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent

EXPECTED_TABLES = {
    "workspaces",
    "document_groups",
    "documents",
    "chunks",
    "entities",
    "entity_aliases",
    "facts",
    "scan_runs",
    "drifts",
    "resolutions",
    "snapshots",
    "snapshot_facts",
    "snapshot_entities",
}


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_upgrade_and_downgrade_clean(database_url: str) -> None:
    cfg = _alembic_config(database_url)

    command.upgrade(cfg, "head")
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES.issubset(tables)
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()
