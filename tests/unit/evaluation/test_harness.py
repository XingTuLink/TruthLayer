"""The harness runs the real detectors on cases with no DB or LLM."""

from __future__ import annotations

from truthlayer.domain.enums import DriftType
from truthlayer.evaluation.harness import run_case
from truthlayer.evaluation.schema import (
    CaseCategory,
    ExpectedDrift,
    QACase,
    QADocument,
    QAEntity,
    QAFact,
)


def _two_doc_conflict_case(category: CaseCategory) -> QACase:
    return QACase(
        id="mini_conflict",
        title="mini",
        category=category,
        documents=[
            QADocument(id="da", filename="a.md"),
            QADocument(id="db", filename="b.md"),
        ],
        entities=[QAEntity(id="e", name="某产品")],
        facts=[
            QAFact(
                id="f1", subject="e", predicate="price",
                value=1, value_type="number", source="da",
            ),
            QAFact(
                id="f2", subject="e", predicate="price",
                value=2, value_type="number", source="db",
            ),
        ],
        expected_drifts=(
            [ExpectedDrift(type=DriftType.CONFLICT, predicate="price")]
            if category is CaseCategory.POSITIVE
            else []
        ),
    )


def test_positive_case_detected():
    result = run_case(_two_doc_conflict_case(CaseCategory.POSITIVE))
    assert result.passed
    assert result.true_positives == 1
    assert result.false_positives == []
    assert result.false_negatives == []


def test_negative_case_with_real_conflict_is_a_false_positive():
    result = run_case(_two_doc_conflict_case(CaseCategory.NEGATIVE))
    assert not result.passed
    assert result.true_positives == 0
    assert len(result.false_positives) == 1
    assert result.false_positives[0]["type"] == "conflict"


def test_expected_but_absent_is_false_negative():
    case = QACase(
        id="missing_supersession",
        title="mini",
        category=CaseCategory.POSITIVE,
        documents=[
            QADocument(id="da", filename="a.md"),
            QADocument(id="db", filename="b.md"),
        ],
        entities=[],
        facts=[],
        expected_drifts=[ExpectedDrift(type=DriftType.SUPERSEDED)],
    )
    result = run_case(case)
    assert not result.passed
    assert result.total_candidates == 0
    assert result.false_negatives == [0]


def test_evaluation_is_deterministic_across_runs():
    case = _two_doc_conflict_case(CaseCategory.POSITIVE)
    first = run_case(case)
    second = run_case(case)
    assert (first.passed, first.true_positives) == (
        second.passed,
        second.true_positives,
    )
