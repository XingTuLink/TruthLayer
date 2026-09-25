"""Evidence schema tests (#2, #13)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from truthlayer.domain.evidence import Evidence


def _kwargs(**overrides: object) -> dict[str, object]:
    data = {
        "document_id": uuid.uuid4(),
        "chunk_id": uuid.uuid4(),
        "page": 2,
        "offset": 120,
        "quote": "Standard price is ¥99.",
        "source_type": "policy",
        "authority_score": 0.9,
    }
    data.update(overrides)
    return data


def test_valid_evidence() -> None:
    evidence = Evidence.model_validate(_kwargs())
    assert evidence.page == 2
    assert evidence.authority_score == pytest.approx(0.9)


def test_quote_must_not_be_blank() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(_kwargs(quote="   "))


def test_authority_bounds() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(_kwargs(authority_score=1.1))
    with pytest.raises(ValidationError):
        Evidence.model_validate(_kwargs(authority_score=-0.1))


def test_page_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(_kwargs(page=0))


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(_kwargs(unexpected="x"))


def test_evidence_is_frozen() -> None:
    evidence = Evidence.model_validate(_kwargs())
    with pytest.raises(ValidationError):
        evidence.quote = "changed"  # type: ignore[misc]
