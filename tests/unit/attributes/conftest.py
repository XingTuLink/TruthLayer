"""In-memory world builders for attribute-resolution tests."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from truthlayer.detection.state import (
    DocumentView,
    EntityView,
    FactView,
    KnowledgeState,
)

_NAMESPACE = uuid.UUID("abcdef12-1234-5678-1234-567812345678")


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, name)


def pricing_doc(name: str) -> DocumentView:
    return DocumentView(
        id=uid(f"doc:{name}"),
        group_id=None,
        previous_version_id=None,
        filename=name,
        source_type="pricing",
        authority_score=0.9,
        parsed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def policy_doc(name: str) -> DocumentView:
    doc = pricing_doc(name)
    return DocumentView(
        id=doc.id,
        group_id=None,
        previous_version_id=None,
        filename=name,
        source_type="policy",
        authority_score=0.9,
        parsed_at=doc.parsed_at,
    )


def fact(
    fid: str,
    *,
    subject: str = "云客服专业版",
    predicate: str,
    value: Any,
    object_type: str = "number",
    unit: str | None = None,
    currency: str | None = None,
    tax_basis: str | None = None,
    document: str = "02_渠道价格.md",
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> FactView:
    return FactView(
        id=uid(f"fact:{fid}"),
        subject_id=uid(f"entity:{subject}"),
        subject_name=subject,
        predicate=predicate,
        object_value=value,
        object_type=object_type,
        object_entity_id=None,
        object_entity_name=None,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=date(2026, 1, 1),
        confidence=0.9,
        document_id=uid(f"doc:{document}"),
        measure_unit=unit,
        currency=currency,
        tax_basis=tax_basis,
    )


def build_state(
    facts: list[FactView], documents: list[DocumentView]
) -> KnowledgeState:
    entities = {
        f.subject_id: EntityView(
            id=f.subject_id,
            canonical_name=f.subject_name,
            entity_type="product",
            embedding=None,
        )
        for f in facts
    }
    return KnowledgeState(
        workspace_id=uid("workspace"),
        facts=facts,
        documents={doc.id: doc for doc in documents},
        entities=list(entities.values()),
    )
