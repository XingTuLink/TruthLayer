"""Minimal ORM world builders for Sprint 5 integration tests (no LLM)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from truthlayer.db.orm import Chunk, Document, Drift, Entity, Fact, ScanRun, Workspace


def make_workspace(session: Session, name: str) -> Workspace:
    ws = Workspace(name=name)
    session.add(ws)
    session.flush()
    return ws


def make_scan_run(session: Session, ws: Workspace) -> ScanRun:
    run = ScanRun(
        workspace_id=ws.id,
        config_hash="test",
        status="completed",
        started_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        completed_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def make_document(
    session: Session,
    ws: Workspace,
    filename: str,
    *,
    source_type: str = "policy",
    authority: float = 0.9,
    version_label: str | None = None,
) -> tuple[Document, Chunk]:
    document = Document(
        workspace_id=ws.id,
        version_label=version_label,
        filename=filename,
        file_hash=f"hash-{filename}-{uuid.uuid4().hex[:6]}",
        content_hash=f"content-{filename}",
        parsed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status="parsed",
        authority_score=authority,
        metadata_jsonb={"relative_path": filename, "source_type": source_type},
    )
    session.add(document)
    session.flush()
    chunk = Chunk(
        document_id=document.id,
        chunk_index=0,
        text=f"{filename} 的原文",
        token_count=10,
    )
    session.add(chunk)
    session.flush()
    return document, chunk


def make_entity(session: Session, ws: Workspace, name: str) -> Entity:
    entity = Entity(workspace_id=ws.id, canonical_name=name, entity_type="product")
    session.add(entity)
    session.flush()
    return entity


def make_fact(
    session: Session,
    ws: Workspace,
    entity: Entity,
    predicate: str,
    value: object,
    *,
    document: Document,
    chunk: Chunk,
    source_type: str = "policy",
    observed: datetime | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    quote: str | None = None,
) -> Fact:
    fact = Fact(
        workspace_id=ws.id,
        subject_entity_id=entity.id,
        predicate=predicate,
        object_value=value,
        object_type="number" if isinstance(value, (int, float)) else "string",
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=observed,
        confidence=0.9,
        status="active",
        source_chunk_id=chunk.id,
        evidence_jsonb=[
            {
                "document_id": str(document.id),
                "chunk_id": str(chunk.id),
                "quote": quote or f"{entity.canonical_name} 的 {predicate} 是 {value}",
                "source_type": source_type,
                "authority_score": document.authority_score,
            }
        ],
    )
    session.add(fact)
    session.flush()
    return fact


def make_drift(
    session: Session,
    run: ScanRun,
    *,
    drift_type: str,
    severity: str,
    target_type: str = "fact",
    target_id: uuid.UUID,
    detail: dict,
    old_fact: Fact | None = None,
    new_fact: Fact | None = None,
    old_document: Document | None = None,
    new_document: Document | None = None,
    subject: Entity | None = None,
    predicate: str | None = None,
    status: str = "open",
    ai_impact: str | None = None,
    confidence: float = 0.95,
) -> Drift:
    drift = Drift(
        scan_run_id=run.id,
        target_type=target_type,
        target_id=target_id,
        type=drift_type,
        severity=severity,
        status=status,
        subject_entity_id=subject.id if subject else None,
        predicate=predicate,
        old_fact_id=old_fact.id if old_fact else None,
        new_fact_id=new_fact.id if new_fact else None,
        old_document_id=old_document.id if old_document else None,
        new_document_id=new_document.id if new_document else None,
        detector_type=f"{drift_type}_detector",
        ai_impact_level=ai_impact or severity,
        detected_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        confidence=confidence,
        detail_jsonb=detail,
    )
    session.add(drift)
    session.flush()
    return drift
