"""Opt-in smoke: real Ollama (qwen2.5 + bge-m3) end to end.

Skipped unless BOTH hold:
* TRUTHLAYER_RUN_OLLAMA=1
* TRUTHLAYER_DATABASE_URL points at PostgreSQL (pgvector enabled)
and a local Ollama server serves the two models on :11434.

This is a quality smoke (real extraction output), not a deterministic
assertion suite — the fake-provider integration tests cover correctness.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from truthlayer.config import TruthLayerConfig
from truthlayer.db.orm import Fact
from truthlayer.extraction.service import KnowledgeExtractionService
from truthlayer.ingestion.service import DocumentIngestionService
from truthlayer.providers.openai_compatible import (
    OpenAICompatibleEmbedder,
    OpenAICompatibleLLM,
)

pytestmark = [pytest.mark.integration, pytest.mark.ollama]

PROJECT_ROOT = Path(__file__).resolve().parent
while not (PROJECT_ROOT / "alembic.ini").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent

OLLAMA_URL = "http://localhost:11434/v1"


def _ollama_reachable() -> bool:
    import json as json_lib
    from urllib.request import urlopen

    try:
        with urlopen(
            "http://localhost:11434/api/tags", timeout=5
        ) as response:
            payload = json_lib.loads(response.read())
        names = {m["name"] for m in payload.get("models", [])}
        return any(n.startswith("qwen2.5") for n in names) and any(
            n.startswith("bge-m3") for n in names
        )
    except Exception:  # noqa: BLE001 - any failure means skip
        return False


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


def test_ollama_real_extraction(
    session: Session, tmp_path: Path
) -> None:
    if os.environ.get("TRUTHLAYER_RUN_OLLAMA") != "1":
        pytest.skip("set TRUTHLAYER_RUN_OLLAMA=1 to run the Ollama smoke")
    if not _ollama_reachable():
        pytest.skip("local Ollama with qwen2.5/bge-m3 not reachable")

    workspace = tmp_path / "kb"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "pricing.txt").write_text(
        "产品价格表（2026年1月1日生效）：\n"
        "ACME CRM Pro 每用户每月 149 元。\n"
        "ACME CRM Enterprise 每用户每月 329 元。\n",
        encoding="utf-8",
    )
    config = TruthLayerConfig.model_validate(
        {
            "workspace": {"name": f"ollama-{uuid.uuid4().hex[:8]}"},
            "sources": [
                {"path": "./docs", "authority": 0.9, "type": "pricing"}
            ],
            "extraction": {
                "provider": "ollama",
                "model": "qwen2.5:7b",
                "base_url": OLLAMA_URL,
            },
            "embedding": {
                "provider": "ollama",
                "model": "bge-m3",
                "base_url": OLLAMA_URL,
            },
        }
    )

    ingestion = DocumentIngestionService(session, config, workspace).run()
    assert ingestion.parsed == 1

    llm = OpenAICompatibleLLM(model="qwen2.5:7b", base_url=OLLAMA_URL)
    embedder = OpenAICompatibleEmbedder(
        model="bge-m3", base_url=OLLAMA_URL
    )
    result = KnowledgeExtractionService(
        session, config, workspace, llm, embedder
    ).run()

    assert result.facts_total >= 1
    facts = session.scalars(select(Fact)).all()
    price_facts = [
        f
        for f in facts
        if f.predicate in {"list_price", "价格"}
        or (f.object_value in (149, 329))
    ]
    assert price_facts, "expected at least one extracted price fact"
    for fact in price_facts:
        assert fact.evidence_jsonb
        assert fact.evidence_jsonb[0]["quote"]
    assert result.embedding_dim == 1024
    assert result.knowledge_hash
