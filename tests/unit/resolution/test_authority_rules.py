"""Authority-fact selection rules for ResolutionService.resolve (#27).

These rules are pure; persistence lifecycle is covered in PG integration.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from truthlayer.domain.enums import ResolutionDecision
from truthlayer.domain.errors import DomainValidationError
from truthlayer.resolution.service import ResolutionService


def _drift(old=..., new=...):  # type: ignore[no-untyped-def]
    old_id = uuid.uuid4() if old is ... else old
    new_id = uuid.uuid4() if new is ... else new
    return SimpleNamespace(old_fact_id=old_id, new_fact_id=new_id)


resolve_authority = ResolutionService._resolve_authority


def test_decision_defaults_to_matching_side() -> None:
    drift = _drift()
    assert (
        resolve_authority(
            drift, ResolutionDecision.ACCEPT_NEWER, None
        )
        == drift.new_fact_id
    )
    assert (
        resolve_authority(drift, ResolutionDecision.KEEP_OLD, None)
        == drift.old_fact_id
    )


def test_manual_and_false_positive_have_no_authority() -> None:
    drift = _drift()
    assert (
        resolve_authority(drift, ResolutionDecision.MANUAL_OVERRIDE, None)
        is None
    )
    assert (
        resolve_authority(drift, ResolutionDecision.FALSE_POSITIVE, None)
        is None
    )


def test_accept_newer_without_new_fact_yields_none() -> None:
    drift = _drift(old=uuid.uuid4(), new=None)
    assert (
        resolve_authority(
            drift, ResolutionDecision.ACCEPT_NEWER, None
        )
        is None
    )


def test_explicit_authority_must_belong_to_drift() -> None:
    drift = _drift()
    foreign = uuid.uuid4()
    with pytest.raises(DomainValidationError, match="old/new facts"):
        resolve_authority(
            drift, ResolutionDecision.MANUAL_OVERRIDE, foreign
        )

    # The old/new pointers themselves are accepted on any decision.
    assert (
        resolve_authority(
            drift,
            ResolutionDecision.FALSE_POSITIVE,
            drift.old_fact_id,
        )
        == drift.old_fact_id
    )


def test_explicit_authority_rejected_for_document_level_drift() -> None:
    drift = SimpleNamespace(old_fact_id=None, new_fact_id=None)
    with pytest.raises(DomainValidationError, match="documents, not facts"):
        resolve_authority(
            drift, ResolutionDecision.ACCEPT_NEWER, uuid.uuid4()
        )
