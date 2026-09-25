"""Deterministic Knowledge Hash tests (#5, #30)."""

from __future__ import annotations

from datetime import date

from truthlayer.extraction.knowledge_hash import (
    compute_knowledge_hash,
    fact_record,
)
from truthlayer.db.orm import Entity, Fact


def _entity(name: str, kind: str = "product") -> Entity:
    return Entity(canonical_name=name, entity_type=kind)


def _fact(**overrides) -> Fact:
    base = dict(
        subject_entity_id=None,
        predicate="list_price",
        object_entity_id=None,
        object_value=149,
        object_type="number",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        status="active",
    )
    base.update(overrides)
    return Fact(**base)


def _records():
    a = _entity("ACME CRM Pro")
    b = _entity("ACME CRM Enterprise")
    return [
        fact_record(_fact(), subject=a, object_entity=None),
        fact_record(
            _fact(predicate="list_price", object_value=329),
            subject=b,
            object_entity=None,
        ),
    ]


def test_hash_is_stable_sha256_hex() -> None:
    digest = compute_knowledge_hash(_records())
    assert digest == compute_knowledge_hash(_records())
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_independent_of_record_order() -> None:
    records = _records()
    assert compute_knowledge_hash(records) == compute_knowledge_hash(
        list(reversed(records))
    )


def test_hash_changes_with_content() -> None:
    base = _records()
    changed = [dict(base[0]), dict(base[1])]
    changed[0]["object_value"] = 159
    assert compute_knowledge_hash(base) != compute_knowledge_hash(changed)


def test_hash_changes_with_status() -> None:
    records = _records()
    retired = [
        dict(records[0], status="superseded"),
        dict(records[1]),
    ]
    assert compute_knowledge_hash(records) != compute_knowledge_hash(retired)


def test_empty_knowledge_hashes_to_constant() -> None:
    assert compute_knowledge_hash([]) == compute_knowledge_hash([])


def test_entity_object_participates_in_record() -> None:
    subject = _entity("ACME CRM Pro")
    target = _entity("ACME CRM Max", "product")
    record = fact_record(
        _fact(
            object_value=None,
            object_type=None,
            object_entity_id=target.id,
        ),
        subject=subject,
        object_entity=target,
    )
    assert record["object_entity"] == "ACME CRM Max"
    assert record["object_value"] is None
