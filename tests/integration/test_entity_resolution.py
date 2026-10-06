"""Integration tests for deterministic entity resolution (R8).

Covers the R8 merge: a same-name entity declared with a type that only drifts
within the controlled ``offering`` class (product<->service) resolves to one
row across sources, while same-name entities from different semantic
categories stay split and an already-ambiguous name is never guessed.

Runs against the throwaway ``*_it`` database (see conftest.database_url).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from truthlayer.db.orm import Entity, EntityAlias, Workspace
from truthlayer.extraction.entities import EntityResolver
from truthlayer.extraction.schemas import RawEntity

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent
while not (PROJECT_ROOT / "alembic.ini").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent


@pytest.fixture
def session(database_url: str) -> Iterator[Session]:
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
def resolver(session: Session) -> EntityResolver:
    workspace = Workspace(name=f"er-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    session.flush()
    return EntityResolver(session, workspace.id)


def _entity_rows(session: Session, name: str) -> list[Entity]:
    return list(
        session.scalars(
            select(Entity).where(Entity.canonical_name == name)
        ).all()
    )


@pytest.mark.parametrize(
    "first_type,second_type",
    [("service", "product"), ("product", "service")],
)
def test_offering_type_drift_merges_same_name(
    resolver: EntityResolver,
    session: Session,
    first_type: str,
    second_type: str,
) -> None:
    name = "数据迁移服务"
    first = resolver.resolve(
        RawEntity(name=name, type=first_type, aliases=["迁移服务"])
    )
    second = resolver.resolve(RawEntity(name=name, type=second_type))

    assert second.id == first.id
    assert resolver.created_count == 1
    # The first-seen concrete type label is retained.
    assert first.entity_type == first_type
    rows = _entity_rows(session, name)
    assert len(rows) == 1
    # The surface form from the second source is still recorded as an alias.
    alias_texts = set(
        session.scalars(
            select(EntityAlias.alias_text).where(
                EntityAlias.entity_id == first.id
            )
        ).all()
    )
    assert "迁移服务" in alias_texts
    assert name in alias_texts


def test_exact_same_name_and_type_remains_one(
    resolver: EntityResolver,
) -> None:
    a = resolver.resolve(RawEntity(name="云存储服务", type="product"))
    b = resolver.resolve(RawEntity(name="云存储服务", type="product"))
    assert a.id == b.id
    assert resolver.created_count == 1


@pytest.mark.parametrize(
    "first_type,second_type",
    [
        ("person", "product"),
        ("org", "service"),
        ("policy", "product"),
        ("unknown", "product"),
        ("customer", "service"),
    ],
)
def test_same_name_across_semantic_categories_stays_split(
    resolver: EntityResolver,
    session: Session,
    first_type: str,
    second_type: str,
) -> None:
    name = "同名对象"
    a = resolver.resolve(RawEntity(name=name, type=first_type))
    b = resolver.resolve(RawEntity(name=name, type=second_type))

    assert a.id != b.id
    assert resolver.created_count == 2
    assert len(_entity_rows(session, name)) == 2


def test_already_ambiguous_same_name_is_not_guessed(
    resolver: EntityResolver,
    session: Session,
) -> None:
    name = "标准版"
    person = resolver.resolve(RawEntity(name=name, type="person"))
    product = resolver.resolve(RawEntity(name=name, type="product"))
    # History is already split across two semantic categories. A later
    # "service" declaration must not be silently attached to either one.
    service = resolver.resolve(RawEntity(name=name, type="service"))

    assert service.id not in {person.id, product.id}
    assert resolver.created_count == 3
    assert len(_entity_rows(session, name)) == 3


def test_distinct_names_are_distinct_entities(
    resolver: EntityResolver,
    session: Session,
) -> None:
    hq = resolver.resolve(RawEntity(name="星云科技客服中心", type="customer"))
    branch = resolver.resolve(
        RawEntity(name="星云科技客服中心（高新分部）", type="customer")
    )
    assert hq.id != branch.id
    assert resolver.created_count == 2
    assert session.scalar(select(func.count()).select_from(Entity)) == 2
