"""Fact domain rule tests (#10, #11)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from truthlayer.domain.enums import ObjectType
from truthlayer.domain.fact import FactClaim


def _entity_claim(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "subject_entity_id": uuid.uuid4(),
        "predicate": "reports_to",
        "object_entity_id": uuid.uuid4(),
    }
    data.update(overrides)
    return data


def _scalar_claim(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "subject_entity_id": uuid.uuid4(),
        "predicate": "price",
        "object_value": 99,
        "object_type": "number",
    }
    data.update(overrides)
    return data


def test_entity_object_valid() -> None:
    claim = FactClaim.model_validate(_entity_claim())
    assert claim.object_entity_id is not None
    assert claim.object_value is None
    assert claim.object_type is None


def test_scalar_object_valid() -> None:
    claim = FactClaim.model_validate(_scalar_claim())
    assert claim.object_value == 99
    assert claim.object_type is ObjectType.NUMBER


def test_neither_object_is_invalid() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        FactClaim.model_validate(
            {"subject_entity_id": uuid.uuid4(), "predicate": "price"}
        )


def test_both_objects_are_invalid() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        FactClaim.model_validate(
            _scalar_claim(object_entity_id=uuid.uuid4())
        )


def test_entity_object_cannot_have_object_type() -> None:
    with pytest.raises(ValidationError, match="object_type"):
        FactClaim.model_validate(
            _entity_claim(object_type="string")
        )


def test_scalar_requires_object_type() -> None:
    with pytest.raises(ValidationError, match="object_type"):
        FactClaim.model_validate(_scalar_claim(object_type=None))


def test_scalar_type_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        FactClaim.model_validate(
            _scalar_claim(object_value="99", object_type="number")
        )


def test_boolean_is_not_treated_as_number() -> None:
    with pytest.raises(ValidationError):
        FactClaim.model_validate(
            _scalar_claim(object_value=True, object_type="number")
        )


def test_date_scalar_type() -> None:
    claim = FactClaim.model_validate(
        _scalar_claim(
            object_value=date(2026, 1, 1), object_type="date"
        )
    )
    assert claim.object_type is ObjectType.DATE


def test_valid_window_order() -> None:
    claim = FactClaim.model_validate(
        _scalar_claim(
            valid_from=date(2025, 1, 1), valid_to=date(2026, 1, 1)
        )
    )
    assert claim.valid_from <= claim.valid_to  # type: ignore[operator]


def test_inverted_window_rejected() -> None:
    with pytest.raises(ValidationError, match="valid_from"):
        FactClaim.model_validate(
            _scalar_claim(
                valid_from=date(2026, 6, 1), valid_to=date(2026, 1, 1)
            )
        )
