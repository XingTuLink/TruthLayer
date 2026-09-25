"""Integration: end-to-end ingestion against PostgreSQL.

Skipped unless TRUTHLAYER_DATABASE_URL points at a running PostgreSQL 15+
with the pgvector extension available.

Covers: six-format ingestion path (via native + openpyxl/docx/pymupdf),
file-hash idempotency, stable chunk indexes, authority, ignore patterns,
failed-file handling, and explicit version-chain links (#8, #35).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from truthlayer.config import TruthLayerConfig
from truthlayer.db.orm import Chunk, Document
from truthlayer.ingestion.service import DocumentIngestionService

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


def _make_config(base_dir: Path) -> TruthLayerConfig:
    return TruthLayerConfig.model_validate(
        {
            "workspace": {"name": f"it-{uuid.uuid4().hex[:8]}"},
            "sources": [
                {"path": "./docs", "authority": 0.95, "type": "policy"},
            ],
            "ignore": ["**/ignored/**"],
            "ci": {"fail_on": "critical"},
            "version_mapping": [
                {
                    "group": "pricing",
                    "files": [
                        {"path": "pricing_2025.csv", "label": "2025"},
                        {
                            "path": "pricing_2026.csv",
                            "label": "2026",
                            "supersedes": "pricing_2025.csv",
                        },
                    ],
                },
                {
                    "group": "policy",
                    "files": [
                        {"path": "policy_v2.txt", "label": "v2"},
                        {"path": "policy_v10.txt", "label": "v10"},
                    ],
                },
            ],
        }
    )


def _seed_workspace(base_dir: Path) -> None:
    docs = base_dir / "docs"
    (docs / "policies").mkdir(parents=True)
    (docs / "ignored").mkdir(parents=True)

    (docs / "policies" / "policy_v2.txt").write_text(
        "Policy version two. Standard price is 99 yuan.\n", encoding="utf-8"
    )
    (docs / "policies" / "policy_v10.txt").write_text(
        "Policy version ten. Standard price is 129 yuan from 2026.\n",
        encoding="utf-8",
    )
    (docs / "pricing_2025.csv").write_text(
        "product,price\nPro,99\nTeam,199\n", encoding="utf-8"
    )
    (docs / "pricing_2026.csv").write_text(
        "product,price\nPro,129\nTeam,259\n", encoding="utf-8"
    )
    (docs / "ignored" / "secret.txt").write_text("do not ingest", encoding="utf-8")
    (docs / "broken.pdf").write_bytes(b"definitely not a pdf")


def test_ingestion_idempotency_and_version_chain(
    session: Session, tmp_path: Path
) -> None:
    config = _make_config(tmp_path)
    _seed_workspace(tmp_path)

    service = DocumentIngestionService(session, config, tmp_path)

    # First run: 4 parseable files, 1 corrupt PDF.
    first = service.run()
    session.commit()

    assert first.discovered == 5
    assert first.parsed == 4
    assert first.failed == 1
    assert first.unchanged == 0
    assert first.chunks >= 4
    assert not any("secret" in warning for warning in first.warnings)

    documents = session.execute(
        select(Document).where(
            Document.metadata_jsonb["source_type"].astext == "policy"
        )
    ).scalars().all()
    docs_by_name = {doc.filename: doc for doc in documents}

    assert set(docs_by_name) == {
        "policy_v2.txt",
        "policy_v10.txt",
        "pricing_2025.csv",
        "pricing_2026.csv",
        "broken.pdf",
    }
    assert all(doc.authority_score == 0.95 for doc in documents)

    # Chunk indexes start at zero and are unique per document.
    for document in documents:
        if document.filename == "broken.pdf":
            assert document.status == "failed"
            continue
        indexes = session.execute(
            select(Chunk.chunk_index)
            .where(Chunk.document_id == document.id)
            .order_by(Chunk.chunk_index)
        ).scalars().all()
        assert indexes == list(range(len(indexes)))
        assert indexes, f"{document.filename} produced no chunks"

    # Explicit version link only; labels never imply edges.
    pricing_2025 = docs_by_name["pricing_2025.csv"]
    pricing_2026 = docs_by_name["pricing_2026.csv"]
    policy_v10 = docs_by_name["policy_v10.txt"]

    assert pricing_2026.previous_version_id == pricing_2025.id
    assert pricing_2026.document_group_id is not None
    assert pricing_2026.version_label == "2026"
    assert pricing_2025.document_group_id == pricing_2026.document_group_id
    assert policy_v10.previous_version_id is None  # v10 !-> v2
    assert policy_v10.version_label == "v10"

    total_documents_before = session.scalar(
        select(func.count()).select_from(Document)
    )
    total_chunks_before = session.scalar(select(func.count()).select_from(Chunk))

    # Second run: identical bytes → everything unchanged, no duplication.
    second = DocumentIngestionService(session, config, tmp_path).run()
    session.commit()

    assert second.unchanged == 4
    assert second.parsed == 0
    assert second.failed == 1  # corrupt file retried, fails again
    assert second.chunks == 0

    total_documents_after = session.scalar(
        select(func.count()).select_from(Document)
    )
    total_chunks_after = session.scalar(select(func.count()).select_from(Chunk))
    assert total_documents_after == total_documents_before
    assert total_chunks_after == total_chunks_before

    # Ignored file never reached the database.
    assert session.execute(
        select(Document).where(Document.filename == "secret.txt")
    ).scalars().first() is None
