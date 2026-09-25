"""Deterministic Knowledge Hash (#5, #30).

Every active fact is reduced to a canonical JSON record; records are
serialized with sorted keys, *then* the serialized strings are sorted, so
the hash is independent of database row order, insertion order and dict
iteration order. Only fact content matters — ids, timestamps, embeddings
and confidence metadata are deliberately excluded.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from truthlayer.db.orm import Entity, Fact


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def fact_record(
    fact: Fact,
    *,
    subject: Entity,
    object_entity: Entity | None,
) -> dict[str, Any]:
    """Project an ORM Fact onto its hashable content."""
    return {
        "subject": subject.canonical_name,
        "subject_type": subject.entity_type,
        "predicate": fact.predicate,
        "object_entity": object_entity.canonical_name if object_entity else None,
        "object_entity_type": object_entity.entity_type if object_entity else None,
        "object_value": fact.object_value,
        "object_type": fact.object_type,
        "valid_from": fact.valid_from.isoformat() if fact.valid_from else None,
        "valid_to": fact.valid_to.isoformat() if fact.valid_to else None,
        "status": fact.status,
    }


def compute_knowledge_hash(records: list[dict[str, Any]]) -> str:
    """SHA-256 over sorted canonical records; empty knowledge hashes too."""
    serialized = sorted(canonical_json(record) for record in records)
    digest = hashlib.sha256()
    for item in serialized:
        digest.update(item.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
