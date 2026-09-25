"""Integration: extraction, evidence, embeddings and snapshots on PostgreSQL.

Uses deterministic Fake providers (no network); real Ollama coverage lives
in test_ollama_smoke.py (opt-in). Covers:

* candidate validation + Entity/Alias/Fact persistence
* Evidence First: every fact traces to document/chunk, authority preserved
* invalid/ambiguous candidates rejected, never guessed
* chunk + entity embeddings filled with provider dimensionality
* incremental idempotency and content-addressed immutable snapshots
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
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
    Entity,
    EntityAlias,
    Fact,
    ScanRun,
    Snapshot,
    SnapshotEntity,
    SnapshotFact,
)
from truthlayer.extraction.schemas import ExtractionEnvelope, RawEntity, RawFact
from truthlayer.extraction.service import KnowledgeExtractionService
from truthlayer.ingestion.service import DocumentIngestionService

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parent
while not (PROJECT_ROOT / "alembic.ini").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent


class FakeLLM:
    """Deterministic 'model': emits known candidates from marker text."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_structured(
        self, *, input_text: str, output_schema, system_prompt=None, model=None
    ) -> ExtractionEnvelope:
        self.calls += 1
        entities: list[RawEntity] = []
        facts: list[RawFact] = []

        if "149" in input_text:
            entities = [
                RawEntity(name="ACME CRM Pro", type="product", aliases=["Pro"]),
                RawEntity(name="ACME CRM Max", type="product"),
            ]
            facts = [
                RawFact(
                    subject="ACME CRM Pro",
                    predicate="list_price",
                    object_value=149,
                    object_type="number",
                    valid_from="2026-01-01",
                    quote="ACME CRM Pro 每用户每月 149 元",
                    confidence=0.95,
                ),
                RawFact(
                    subject="ACME CRM Pro",
                    predicate="includes_module",
                    object_entity="ACME CRM Max",
                    quote="ACME CRM Pro 包含 ACME CRM Max 模块",
                ),
                # Undeclared subject: salvaged as type "unknown", warned.
                RawFact(
                    subject="幽灵客户",
                    predicate="risk_level",
                    object_value="high",
                    object_type="string",
                    quote="产品价格表",
                ),
                # Deliberately bad: quote that is not in the source.
                RawFact(
                    subject="ACME CRM Pro",
                    predicate="currency",
                    object_value="CNY",
                    object_type="string",
                    quote="一段原文里根本没有的话",
                ),
                # Deliberately bad: JSON string claims to be a number.
                RawFact(
                    subject="ACME CRM Pro",
                    predicate="tax_rate",
                    object_value="17%",
                    object_type="number",
                    quote="ACME CRM Pro 每用户每月 149 元",
                ),
            ]
        if "329" in input_text:
            entities = [RawEntity(name="ACME CRM Enterprise", type="product")]
            facts = [
                RawFact(
                    subject="ACME CRM Enterprise",
                    predicate="list_price",
                    object_value=329,
                    object_type="number",
                    quote="ACME CRM Enterprise 每用户每月 329 元",
                )
            ]
        return ExtractionEnvelope(entities=entities, facts=facts)


class FakeEmbedder:
    def __init__(self, dim: int = 16) -> None:
        self.dimensions = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append(
                [b / 255.0 for b in digest[: self.dimensions]]
            )
        return vectors


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


def _config(workspace_name: str) -> TruthLayerConfig:
    return TruthLayerConfig.model_validate(
        {
            "workspace": {"name": workspace_name},
            "sources": [
                {"path": "./docs", "authority": 0.95, "type": "policy"},
            ],
            "extraction": {
                "provider": "fake",
                "model": "fake-llm",
            },
            "embedding": {
                "provider": "fake",
                "model": "fake-embed",
                "dimensions": 16,
            },
        }
    )


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, TruthLayerConfig]:
    config = _config(f"it-{uuid.uuid4().hex[:8]}")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "pricing_2026.txt").write_text(
        "产品价格表（2026年1月1日生效）：\n"
        "ACME CRM Pro 每用户每月 149 元。\n"
        "ACME CRM Pro 包含 ACME CRM Max 模块。\n",
        encoding="utf-8",
    )
    return tmp_path, config


def _run_both(session: Session, base_dir: Path, config: TruthLayerConfig):
    ingestion = DocumentIngestionService(session, config, base_dir).run()
    extraction = KnowledgeExtractionService(
        session, config, base_dir, FakeLLM(), FakeEmbedder()
    ).run()
    return ingestion, extraction


def test_extraction_end_to_end(
    session: Session, workspace: tuple[Path, TruthLayerConfig]
) -> None:
    base_dir, config = workspace
    ingestion, result = _run_both(session, base_dir, config)

    assert ingestion.parsed == 1
    # 5 candidates: 4 persist (1 unknown salvage, 1 fallback quote), 1 rejected.
    assert result.facts_new == 4
    assert result.facts_total == 4
    assert result.entities_new == 3
    assert result.chunks_processed == 1
    assert result.chunk_embeddings == 1
    assert result.entity_embeddings == 3
    assert result.embedding_dim == 16
    assert len(result.errors) == 1
    assert "number" in result.errors[0][1]
    salvage_warnings = [
        w for w in result.warnings if "auto-typed 'unknown'" in w
    ]
    assert salvage_warnings and "幽灵客户" in salvage_warnings[0]
    assert any("quote not found verbatim" in w for w in result.warnings)

    unknown = session.scalars(
        select(Entity).where(Entity.entity_type == "unknown")
    ).all()
    assert [e.canonical_name for e in unknown] == ["幽灵客户"]

    # ScanRun is fully recorded (#15).
    run = session.get(ScanRun, result.scan_run_id)
    assert run.status == "completed"
    assert run.extraction_provider == "fake"
    assert run.extraction_model == "fake-llm"
    assert run.embedding_model == "fake-embed"
    assert run.embedding_dim == 16
    assert run.prompt_version.startswith("fact-extract-")
    assert run.config_hash and len(run.config_hash) == 64
    assert run.fact_count == 4

    # Every fact traces to Evidence inside the source document (#2).
    facts = session.scalars(
        select(Fact).where(Fact.workspace_id == run.workspace_id)
    ).all()
    assert len(facts) == 4
    by_predicate = {fact.predicate: fact for fact in facts}
    price = by_predicate["list_price"]
    assert price.object_value == 149
    assert price.object_type == "number"
    assert price.valid_from.isoformat() == "2026-01-01"
    assert price.confidence == 0.95
    assert len(price.evidence_jsonb) == 1
    evidence = price.evidence_jsonb[0]
    assert evidence["quote"] == "ACME CRM Pro 每用户每月 149 元"
    assert evidence["authority_score"] == 0.95
    assert evidence["source_type"] == "policy"
    # ...and the pointers resolve to real rows.
    assert session.get(Document, uuid.UUID(evidence["document_id"])) is not None
    assert session.get(Chunk, uuid.UUID(evidence["chunk_id"])) is not None

    # Fallback quote for the non-verbatim candidate anchors to chunk text.
    currency = by_predicate["currency"]
    assert "ACME CRM Pro" in currency.evidence_jsonb[0]["quote"]

    # Entity-object fact has NULL scalar, real FK object.
    includes = by_predicate["includes_module"]
    assert includes.object_entity_id is not None
    assert includes.object_value is None

    # Aliases persisted.
    aliases = session.scalars(select(EntityAlias.alias_text)).all()
    assert "Pro" in aliases
    assert "ACME CRM Pro" in aliases

    # Embeddings actually landed with the provider dimension.
    chunk_vec = session.scalars(select(Chunk.embedding)).first()
    assert len(chunk_vec) == 16
    entity_vecs = session.scalars(select(Entity.embedding)).all()
    assert all(len(v) == 16 for v in entity_vecs)

    # Snapshot + membership rows exist and counts agree (#29, #36).
    snapshot = session.get(Snapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.fact_count == 4
    assert snapshot.entity_count == 3
    assert snapshot.knowledge_hash == result.knowledge_hash
    assert (
        session.scalar(
            select(func.count()).select_from(SnapshotFact)
        )
        == 4
    )
    assert (
        session.scalar(
            select(func.count()).select_from(SnapshotEntity)
        )
        == 3
    )


def test_second_scan_is_idempotent_same_hash(
    session: Session, workspace: tuple[Path, TruthLayerConfig]
) -> None:
    base_dir, config = workspace
    _, first = _run_both(session, base_dir, config)
    _, second = _run_both(session, base_dir, config)

    assert second.chunks_processed == 0
    assert second.facts_new == 0
    assert second.facts_total == 4
    assert second.chunk_embeddings == 0
    assert second.entity_embeddings == 0
    assert second.knowledge_hash == first.knowledge_hash

    # Two snapshots, same hash, no duplicated facts.
    runs = session.scalars(select(ScanRun)).all()
    assert all(r.status == "completed" for r in runs)
    assert session.scalar(select(func.count()).select_from(Fact)) == 4
    snapshots = session.scalars(select(Snapshot)).all()
    assert len(snapshots) == 2
    assert snapshots[0].knowledge_hash == snapshots[1].knowledge_hash


def test_new_document_changes_knowledge_hash(
    session: Session, workspace: tuple[Path, TruthLayerConfig]
) -> None:
    base_dir, config = workspace
    _, first = _run_both(session, base_dir, config)

    (base_dir / "docs" / "pricing_new.txt").write_text(
        "ACME CRM Enterprise 每用户每月 329 元。\n",
        encoding="utf-8",
    )
    _, second = _run_both(session, base_dir, config)

    assert second.facts_new == 1
    assert second.facts_total == 5
    assert second.chunk_embeddings == 1  # only the new chunk
    assert second.entity_embeddings == 1  # only the new entity
    assert second.knowledge_hash != first.knowledge_hash
    assert session.scalar(select(func.count()).select_from(Fact)) == 5
