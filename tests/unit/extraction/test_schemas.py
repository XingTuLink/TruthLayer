"""Tests for LLM-facing extraction schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from truthlayer.extraction.schemas import (
    ExtractionEnvelope,
    RawEntity,
    RawFact,
)


def _scalar_fact(**overrides) -> RawFact:
    base = dict(
        subject="ACME CRM Pro",
        predicate="list_price",
        object_value=149,
        object_type="number",
        valid_from="2026-01-01",
        quote="ACME CRM Pro 每用户每月 149 元",
    )
    base.update(overrides)
    return RawFact(**base)


def test_scalar_fact_valid() -> None:
    fact = _scalar_fact()
    assert fact.object_value == 149
    assert fact.confidence == 0.7


def test_fact_requires_exactly_one_object() -> None:
    with pytest.raises(ValidationError):
        RawFact(
            subject="A",
            predicate="p",
            object_entity="B",
            object_value=1,
            object_type="number",
            quote="q",
        )
    with pytest.raises(ValidationError):
        RawFact(subject="A", predicate="p", quote="q")


def test_entity_object_forbids_object_type() -> None:
    with pytest.raises(ValidationError):
        RawFact(
            subject="A",
            predicate="p",
            object_entity="B",
            object_type="string",
            quote="q",
        )


def test_scalar_requires_object_type() -> None:
    with pytest.raises(ValidationError):
        RawFact(subject="A", predicate="p", object_value="x", quote="q")


@pytest.mark.parametrize("bad_date", ["2026-1-1", "2026/01/01", "01-01-2026"])
def test_date_must_be_iso(bad_date: str) -> None:
    with pytest.raises(ValidationError):
        _scalar_fact(valid_from=bad_date)


def test_blank_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        _scalar_fact(quote="   ")
    with pytest.raises(ValidationError):
        _scalar_fact(predicate=" ")


def test_entity_aliases_stripped_and_blank_dropped() -> None:
    entity = RawEntity(name="  ACME CRM Pro ", type=" product ", aliases=[" Pro ", " "])
    assert entity.name == "ACME CRM Pro"
    assert entity.type == "product"
    assert entity.aliases == ["Pro"]


def test_envelope_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        ExtractionEnvelope.model_validate(
            {"entities": [], "facts": [], "thoughts": "nope"}
        )


def test_empty_envelope_is_valid() -> None:
    envelope = ExtractionEnvelope()
    assert envelope.entities == []
    assert envelope.facts == []
