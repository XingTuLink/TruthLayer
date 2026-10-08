"""Match detector candidates against expected findings (pure functions).

Matching is intentionally conservative: an expected drift always requires the
same drift type, and any locator fields the case supplies (subject /
predicate / severity / source files) must all agree. Expectations with more
locators are matched first so the most specific claim is satisfied before a
broader one could claim the same candidate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from truthlayer.detection.candidates import DriftCandidate
from truthlayer.domain.enums import DriftType, Severity
from truthlayer.evaluation.schema import ExpectedDrift


@dataclass(frozen=True)
class NormalizedExpectation:
    origin_index: int
    drift_type: DriftType
    locators: int
    subject_id: uuid.UUID | None
    predicate: str | None
    severity: Severity | None
    old_source: str | None
    new_source: str | None
    target: str | None = None

    @classmethod
    def from_expected(
        cls,
        expected: ExpectedDrift,
        origin_index: int,
        entity_ids: dict[str, uuid.UUID],
    ) -> "NormalizedExpectation":
        return cls(
            origin_index=origin_index,
            drift_type=expected.type,
            locators=expected.locator_count(),
            subject_id=(
                entity_ids[expected.subject]
                if expected.subject is not None
                else None
            ),
            predicate=expected.predicate,
            severity=expected.severity,
            old_source=expected.old_source,
            new_source=expected.new_source,
            target=expected.target,
        )


def _candidate_old_source(candidate: DriftCandidate) -> str | None:
    # confirmed_stale records its file under "source"; others use old_source.
    return candidate.detail.get("old_source") or candidate.detail.get("source")


def _candidate_new_source(candidate: DriftCandidate) -> str | None:
    return candidate.detail.get("new_source")


def satisfies(candidate: DriftCandidate, exp: NormalizedExpectation) -> bool:
    if candidate.drift_type is not exp.drift_type:
        return False
    if exp.subject_id is not None and candidate.subject_entity_id != exp.subject_id:
        return False
    if (
        exp.predicate is not None
        and (candidate.predicate or "").casefold() != exp.predicate.casefold()
    ):
        return False
    if exp.severity is not None and candidate.severity is not exp.severity:
        return False
    if exp.old_source is not None and _candidate_old_source(candidate) != exp.old_source:
        return False
    if exp.new_source is not None and _candidate_new_source(candidate) != exp.new_source:
        return False
    if exp.target is not None and candidate.target_type.value != exp.target:
        return False
    return True


def _candidate_sort_key(candidate: DriftCandidate) -> tuple:
    return (
        candidate.drift_type.value,
        candidate.predicate or "",
        str(candidate.subject_entity_id or ""),
        candidate.severity.value,
        str(candidate.target_id),
    )


@dataclass(frozen=True)
class MatchResult:
    #: (expected origin index, matched candidate)
    matches: list[tuple[int, DriftCandidate]]
    unmatched_expectations: list[int]
    unmatched_candidates: list[DriftCandidate]


def match_findings(
    candidates: list[DriftCandidate],
    expectations: list[NormalizedExpectation],
) -> MatchResult:
    ordered_candidates = sorted(candidates, key=_candidate_sort_key)
    # Most specific expectations first; stable order for the rest.
    ordered_expectations = sorted(
        expectations,
        key=lambda e: (
            -e.locators,
            e.drift_type.value,
            e.predicate or "",
            str(e.subject_id or ""),
            e.origin_index,
        ),
    )

    used: set[int] = set()
    matches: list[tuple[int, DriftCandidate]] = []
    for exp in ordered_expectations:
        for idx, candidate in enumerate(ordered_candidates):
            if idx in used:
                continue
            if satisfies(candidate, exp):
                used.add(idx)
                matches.append((exp.origin_index, candidate))
                break

    unmatched_candidates = [
        candidate for idx, candidate in enumerate(ordered_candidates)
        if idx not in used
    ]
    matched_expected = {origin for origin, _ in matches}
    unmatched_expectations = [
        exp.origin_index
        for exp in ordered_expectations
        if exp.origin_index not in matched_expected
    ]
    return MatchResult(
        matches=sorted(matches, key=lambda m: m[0]),
        unmatched_expectations=sorted(unmatched_expectations),
        unmatched_candidates=unmatched_candidates,
    )
