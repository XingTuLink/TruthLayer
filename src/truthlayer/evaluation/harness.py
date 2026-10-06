"""Run QA cases through the real deterministic detectors (#40).

Each case is isolated (its own derived workspace) and evaluated for a single
scan, so the Remember-loop suppression in the persistence service does not
apply — we measure whether the detectors *would* report each problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from truthlayer.detection.candidates import DriftCandidate
from truthlayer.detection.service import default_detectors
from truthlayer.evaluation.matching import (
    NormalizedExpectation,
    match_findings,
)
from truthlayer.evaluation.schema import CaseCategory, QACase
from truthlayer.evaluation.state_builder import build_world


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    title: str
    category: CaseCategory
    passed: bool
    true_positives: int
    #: Candidate descriptions the case did not expect.
    false_positives: list[dict]
    #: Origin indices of expected drifts no candidate produced.
    false_negatives: list[int]
    total_candidates: int


@dataclass
class SuiteResult:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)


def describe_candidate(candidate: DriftCandidate) -> dict:
    detail = candidate.detail
    return {
        "detector": candidate.detector_type,
        "type": candidate.drift_type.value,
        "severity": candidate.severity.value,
        "subject": detail.get("subject"),
        "predicate": candidate.predicate,
        "old_source": detail.get("old_source") or detail.get("source"),
        "new_source": detail.get("new_source"),
        "reason": detail.get("reason"),
    }


def run_case(case: QACase) -> CaseResult:
    world = build_world(case)

    candidates: list[DriftCandidate] = []
    for detector in default_detectors():
        candidates.extend(detector.detect(world.state, world.context))

    expectations = [
        NormalizedExpectation.from_expected(exp, idx, world.entity_ids)
        for idx, exp in enumerate(case.expected_drifts)
    ]
    outcome = match_findings(candidates, expectations)

    return CaseResult(
        case_id=case.id,
        title=case.title,
        category=case.category,
        passed=not outcome.unmatched_candidates
        and not outcome.unmatched_expectations,
        true_positives=len(outcome.matches),
        false_positives=[
            describe_candidate(c) for c in outcome.unmatched_candidates
        ],

        false_negatives=list(outcome.unmatched_expectations),
        total_candidates=len(candidates),
    )


def run_suite(cases: list[QACase]) -> SuiteResult:
    return SuiteResult(results=[run_case(case) for case in cases])
