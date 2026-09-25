"""KnowledgeState — immutable DB view consumed by detectors (#55).

Detectors work against plain frozen dataclasses, never SQLAlchemy sessions,
which keeps them unit-testable and reusable by the future API layer (#67).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from truthlayer.db.orm import Chunk, Document, Entity, Fact
from truthlayer.domain.enums import FactStatus


@dataclass(frozen=True)
class EntityView:
    id: uuid.UUID
    canonical_name: str
    entity_type: str
    embedding: tuple[float, ...] | None


@dataclass(frozen=True)
class DocumentView:
    id: uuid.UUID
    group_id: uuid.UUID | None
    previous_version_id: uuid.UUID | None
    filename: str
    source_type: str
    authority_score: float
    parsed_at: datetime | None


@dataclass(frozen=True)
class FactView:
    id: uuid.UUID
    subject_id: uuid.UUID
    subject_name: str
    predicate: str
    object_value: Any
    object_type: str | None
    object_entity_id: uuid.UUID | None
    object_entity_name: str | None
    valid_from: date | None
    valid_to: date | None
    observed_at: date | None
    confidence: float
    document_id: uuid.UUID | None

    @property
    def age_basis(self) -> date | None:
        """Best 'when this was stated' signal for stale heuristics."""
        return self.observed_at or self.valid_from


@dataclass(frozen=True)
class KnowledgeState:
    workspace_id: uuid.UUID
    facts: list[FactView]
    documents: dict[uuid.UUID, DocumentView]
    entities: list[EntityView]

    def fact_document(self, fact: FactView) -> DocumentView | None:
        if fact.document_id is None:
            return None
        return self.documents.get(fact.document_id)

    def newer_version_exists(self, document_id: uuid.UUID) -> bool:
        """True if any document explicitly declares ``document_id`` as old."""
        return any(
            doc.previous_version_id == document_id for doc in self.documents.values()
        )

    def entity_by_id(self, entity_id: uuid.UUID) -> EntityView | None:
        for entity in self.entities:
            if entity.id == entity_id:
                return entity
        return None


def _coerce_date_value(fact: Fact) -> Any:
    """JSONB returns DATE scalars as ISO strings; restore date instances."""
    value = fact.object_value
    if fact.object_type == "date" and isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    return value


def load_knowledge_state(
    session: Session, workspace_id: uuid.UUID
) -> KnowledgeState:
    """Load active facts + documents + entities for one workspace."""
    documents = session.scalars(
        select(Document).where(Document.workspace_id == workspace_id)
    ).all()
    doc_views = {
        doc.id: DocumentView(
            id=doc.id,
            group_id=doc.document_group_id,
            previous_version_id=doc.previous_version_id,
            filename=doc.filename,
            source_type=str(doc.metadata_jsonb.get("source_type", "document")),
            authority_score=float(doc.authority_score),
            parsed_at=doc.parsed_at,
        )
        for doc in documents
    }

    chunk_to_doc: dict[uuid.UUID, uuid.UUID] = {
        chunk.id: chunk.document_id
        for chunk in session.scalars(
            select(Chunk).where(
                Chunk.document_id.in_(list(doc_views) or [uuid.UUID(int=0)])
            )
        ).all()
    }

    entities = session.scalars(
        select(Entity).where(Entity.workspace_id == workspace_id)
    ).all()
    entity_views = [
        EntityView(
            id=entity.id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            embedding=tuple(entity.embedding) if entity.embedding is not None else None,
        )
        for entity in entities
    ]

    subject_alias = aliased(Entity)
    object_alias = aliased(Entity)
    rows = session.execute(
        select(Fact, subject_alias, object_alias)
        .join(subject_alias, Fact.subject_entity_id == subject_alias.id)
        .outerjoin(object_alias, Fact.object_entity_id == object_alias.id)
        .where(
            Fact.workspace_id == workspace_id,
            Fact.status == FactStatus.ACTIVE.value,
        )
    ).all()

    fact_views: list[FactView] = []
    for fact, subject, object_entity in rows:
        fact_views.append(
            FactView(
                id=fact.id,
                subject_id=subject.id,
                subject_name=subject.canonical_name,
                predicate=fact.predicate.strip(),
                object_value=_coerce_date_value(fact),
                object_type=fact.object_type,
                object_entity_id=fact.object_entity_id,
                object_entity_name=(
                    object_entity.canonical_name if object_entity is not None else None
                ),
                valid_from=fact.valid_from,
                valid_to=fact.valid_to,
                observed_at=(
                    fact.observed_at.date() if fact.observed_at is not None else None
                ),
                confidence=float(fact.confidence),
                document_id=(
                    chunk_to_doc.get(fact.source_chunk_id)
                    if fact.source_chunk_id is not None
                    else None
                ),
            )
        )

    return KnowledgeState(
        workspace_id=workspace_id,
        facts=fact_views,
        documents=doc_views,
        entities=entity_views,
    )
