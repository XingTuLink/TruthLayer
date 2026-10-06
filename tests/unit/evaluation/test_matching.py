"""Matching logic between detector candidates and expected findings."""

from __future__ import annotations

import uuid

from truthlayer.detection.candidates import DriftCandidate
from truthlayer.detection.scoring import ai_impact_for
from truthlayer.domain.enums import DriftType, Severity, TargetType
from truthlayer.evaluation.matching import (
    NormalizedExpectation,
    match_findings,
    satisfies,
)


def _candidate(
    drift_type: DriftType,
    *,
    predicate: str | None = None,
    subject: uuid.UUID | None = None,
    severity: Severity = Severity.HIGH,
    detail: dict | None = None,
) -> DriftCandidate:
    return DriftCandidate(
        detector_type=f"{drift_type.value}_detector",
        drift_type=drift_type,
        target_type=TargetType.FACT,
        target_id=uuid.uuid4(),
        severity=severity,
        ai_impact_level=ai_impact_for(severity),
        confidence=0.95,
        subject_entity_id=subject,
        predicate=predicate,
        detail=detail or {},
    )


def _expectation(
    drift_type: DriftType,
    *,
    index: int = 0,
    locators: int = 0,
    subject: uuid.UUID | None = None,
    predicate: str | None = None,
    severity: Severity | None = None,
    old_source: str | None = None,
    new_source: str | None = None,
) -> NormalizedExpectation:
    return NormalizedExpectation(
        origin_index=index,
        drift_type=drift_type,
        locators=locators,
        subject_id=subject,
        predicate=predicate,
        severity=severity,
        old_source=old_source,
        new_source=new_source,
    )


def test_type_is_required():
    c = _candidate(DriftType.CONFLICT)
    assert satisfies(c, _expectation(DriftType.CONFLICT))
    assert not satisfies(c, _expectation(DriftType.DUPLICATE))


def test_predicate_matches_case_insensitively():
    c = _candidate(DriftType.CONFLICT, predicate="List_Price")
    assert satisfies(c, _expectation(DriftType.CONFLICT, predicate="list_price"))
    assert not satisfies(c, _expectation(DriftType.CONFLICT, predicate="other"))


def test_severity_and_subject_and_sources_filter():
    sid = uuid.uuid4()
    c = _candidate(
        DriftType.CONFIRMED_STALE,
        predicate="price",
        subject=sid,
        severity=Severity.CRITICAL,
        detail={"source": "old.csv", "new_source": "new.csv"},
    )
    assert satisfies(
        c,
        _expectation(
            DriftType.CONFIRMED_STALE,
            subject=sid,
            predicate="price",
            severity=Severity.CRITICAL,
            old_source="old.csv",
            new_source="new.csv",
        ),
    )
    # confirmed_stale stores its file under "source"; the matcher accepts it
    # as old_source.
    assert not satisfies(
        c, _expectation(DriftType.CONFIRMED_STALE, old_source="other.csv")
    )
    assert not satisfies(
        c, _expectation(DriftType.CONFIRMED_STALE, severity=Severity.HIGH)
    )


def test_greedy_match_does_not_reuse_candidates():
    s1 = uuid.uuid4()
    s2 = uuid.uuid4()
    candidates = [
        _candidate(DriftType.DUPLICATE, predicate="a", subject=s1),
        _candidate(DriftType.DUPLICATE, predicate="b", subject=s2),
    ]
    expectations = [
        _expectation(DriftType.DUPLICATE, index=0),
        _expectation(DriftType.DUPLICATE, index=1, predicate="b", subject=s2),
    ]
    result = match_findings(candidates, expectations)
    assert len(result.matches) == 2
    assert result.unmatched_candidates == []
    assert result.unmatched_expectations == []


def test_more_specific_expectation_claims_candidate_first():
    sid = uuid.uuid4()
    only = _candidate(DriftType.CONFLICT, predicate="price", subject=sid)
    specific = _expectation(
        DriftType.CONFLICT, index=1, locators=2, subject=sid, predicate="price"
    )
    broad = _expectation(DriftType.CONFLICT, index=0, locators=0)
    result = match_findings([only], [broad, specific])
    # The single candidate is assigned to the specific expectation; the broad
    # one is left unmatched rather than stealing it.
    assert result.matches == [(1, only)]
    assert result.unmatched_expectations == [0]


def test_unmatched_reported_on_both_sides():
    c = _candidate(DriftType.CONFLICT, predicate="x")
    result = match_findings(
        [c], [_expectation(DriftType.SUPERSEDED, index=0)]
    )
    assert result.matches == []
    assert result.unmatched_expectations == [0]
    assert result.unmatched_candidates == [c]
