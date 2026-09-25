"""Helpers for detector unit tests — in-memory KnowledgeState factories."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

import pytest

from truthlayer.detection.context import DetectionContext
from truthlayer.detection.state import (
    DocumentView,
    EntityView,
    FactView,
    KnowledgeState,
)
from truthlayer.domain.enums import Severity

_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, name)


def make_document(
    name: str,
    *,
    group: str | None = None,
    supersedes: str | None = None,
    source_type: str = "policy",
    authority: float = 0.9,
    parsed: datetime | None = None,
) -> DocumentView:
    return DocumentView(
        id=uid(f"doc:{name}"),
        group_id=uid(f"group:{group}") if group else None,
        previous_version_id=uid(f"doc:{supersedes}") if supersedes else None,
        filename=name,
        source_type=source_type,
        authority_score=authority,
        parsed_at=parsed
        or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def make_fact(
    fid: str,
    *,
    subject: str = "ACME CRM Pro",
    subject_type: str = "product",
    predicate: str = "list_price",
    value: Any = 149,
    object_type: str | None = "number",
    object_entity: str | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    observed_at: date | None = None,
    document: str | None = "price_2025.csv",
    confidence: float = 0.7,
) -> FactView:
    object_eid = uid(f"entity:{object_entity}") if object_entity else None
    return FactView(
        id=uid(f"fact:{fid}"),
        subject_id=uid(f"entity:{subject}"),
        subject_name=subject,
        predicate=predicate,
        object_value=None if object_entity else value,
        object_type=None if object_entity else object_type,
        object_entity_id=object_eid,
        object_entity_name=object_entity,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=observed_at,
        confidence=confidence,
        document_id=uid(f"doc:{document}") if document else None,
    )


def build_state(
    facts: list[FactView],
    documents: list[DocumentView] | None = None,
    extra_entities: list[EntityView] | None = None,
) -> KnowledgeState:
    entity_map: dict[uuid.UUID, EntityView] = {}
    for fact in facts:
        entity_map.setdefault(
            fact.subject_id,
            EntityView(
                id=fact.subject_id,
                canonical_name=fact.subject_name,
                entity_type="product",
                embedding=None,
            ),
        )
        if fact.object_entity_id is not None:
            entity_map.setdefault(
                fact.object_entity_id,
                EntityView(
                    id=fact.object_entity_id,
                    canonical_name=fact.object_entity_name or "object",
                    entity_type="product",
                    embedding=None,
                ),
            )
    for entity in extra_entities or []:
        entity_map[entity.id] = entity
    docs = documents or [
        make_document("price_2025.csv"),
        make_document("price_2026.csv"),
    ]
    return KnowledgeState(
        workspace_id=uid("workspace"),
        facts=facts,
        documents={doc.id: doc for doc in docs},
        entities=list(entity_map.values()),
    )


def make_entity(
    name: str, *, entity_type: str = "product", embedding=None
) -> EntityView:
    return EntityView(
        id=uid(f"entity:{name}"),
        canonical_name=name,
        entity_type=entity_type,
        embedding=tuple(embedding) if embedding is not None else None,
    )


@pytest.fixture
def context() -> DetectionContext:
    return DetectionContext(
        as_of=date(2026, 9, 25),
        stale_after_days=365,
        pricing_stale_days=90,
        severity_overrides={
            "pricing_change": Severity.HIGH,
            "policy_change": Severity.CRITICAL,
        },
        multi_valued_predicates=frozenset(),
    )
