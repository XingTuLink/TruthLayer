"""Phase 0 Gate metric aggregation and strict threshold semantics (#40)."""

from __future__ import annotations

from truthlayer.domain.enums import DriftType
from truthlayer.evaluation.harness import CaseResult, SuiteResult
from truthlayer.evaluation.metrics import (
    GATE_F1,
    GATE_FPR,
    GATE_PRECISION,
    GATE_RECALL,
    compute_metrics,
)
from truthlayer.evaluation.schema import CaseCategory, ExpectedDrift, QACase


def _case(case_id: str, category: CaseCategory, types=()) -> QACase:
    return QACase(
        id=case_id,
        title=case_id,
        category=category,
        expected_drifts=[ExpectedDrift(type=t) for t in types],
    )


def _result(
    case: QACase,
    *,
    tp: int = 0,
    fp: int = 0,
    fn: tuple[int, ...] = (),
    fp_type: str = "conflict",
    total: int | None = None,
) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        title=case.title,
        category=case.category,
        passed=fp == 0 and not fn,
        true_positives=tp,
        false_positives=[
            {"type": fp_type, "detector": f"{fp_type}_detector"} for _ in range(fp)
        ],
        false_negatives=list(fn),
        total_candidates=total if total is not None else tp + fp,
    )


def test_perfect_scores_pass_gate():
    pos1 = _case("p1", CaseCategory.POSITIVE, [DriftType.CONFLICT])
    pos2 = _case("p2", CaseCategory.POSITIVE, [DriftType.DUPLICATE])
    neg = _case("n1", CaseCategory.NEGATIVE)
    cases = [pos1, pos2, neg]
    suite = SuiteResult(
        [_result(pos1, tp=1), _result(pos2, tp=1), _result(neg)]
    )
    m = compute_metrics(suite, cases)
    assert (m.tp, m.fp, m.fn) == (2, 0, 0)
    assert (m.precision, m.recall, m.f1, m.fpr) == (1.0, 1.0, 1.0, 0.0)
    assert m.gate.passed


def test_precision_recall_f1_arithmetic():
    case = _case("p", CaseCategory.POSITIVE, [DriftType.CONFLICT, DriftType.CONFLICT])
    suite = SuiteResult([_result(case, tp=1, fp=1, fn=(1,))])
    m = compute_metrics(suite, [case])
    assert m.precision == 0.5
    assert m.recall == 0.5
    assert abs(m.f1 - 0.5) < 1e-9


def test_empty_suite_does_not_crash_and_fails_gate():
    m = compute_metrics(SuiteResult([]), [])
    assert (m.precision, m.recall, m.f1, m.fpr) == (0.0, 0.0, 0.0, 0.0)
    assert not m.gate.passed
    assert m.gate.checks["non_empty_positive"] is False
    assert m.gate.checks["non_empty_benign"] is False


def test_precision_equal_to_threshold_is_failure():
    # 8 TP / (8 TP + 2 FP) = 0.80 exactly; Gate requires strictly > 0.80.
    case = _case("p", CaseCategory.POSITIVE, [DriftType.CONFLICT] * 10)
    neg = _case("n", CaseCategory.NEGATIVE)
    suite = SuiteResult([_result(case, tp=8, fp=2, fn=(8, 9)), _result(neg)])
    m = compute_metrics(suite, [case, neg])
    assert m.precision == GATE_PRECISION
    assert m.gate.checks["precision_gt_0.80"] is False
    assert not m.gate.passed


def test_fpr_equal_to_threshold_is_failure():
    # 3 false alarms among 10 benign scenarios = 0.30 exactly; must be < 0.30.
    cases = [_case(f"n{i}", CaseCategory.NEGATIVE) for i in range(10)]
    results = []
    for i, case in enumerate(cases):
        results.append(_result(case, fp=1, total=1) if i < 3 else _result(case))
    m = compute_metrics(SuiteResult(results), cases)
    assert m.fpr == GATE_FPR
    assert m.gate.checks["fpr_lt_0.30"] is False


def test_threshold_constants_match_brief():
    assert (GATE_PRECISION, GATE_RECALL, GATE_F1, GATE_FPR) == (0.80, 0.70, 0.75, 0.30)


def test_per_detector_and_category_breakdown():
    pc = _case(
        "pc", CaseCategory.POSITIVE, [DriftType.CONFLICT, DriftType.CONFLICT]
    )
    pd = _case("pd", CaseCategory.POSITIVE, [DriftType.DUPLICATE])
    neg = _case("n", CaseCategory.NEGATIVE)
    amb = _case("a", CaseCategory.AMBIGUOUS_NEGATIVE)
    cases = [pc, pd, neg, amb]
    suite = SuiteResult(
        [
            _result(pc, tp=1, fp=1, fn=(1,)),  # 1 conflict found, 1 missed
            _result(pd, tp=1),
            _result(neg),
            _result(amb),
        ]
    )
    m = compute_metrics(suite, cases)
    assert m.per_detector["conflict"].tp == 1
    assert m.per_detector["conflict"].fn == 1
    assert m.per_detector["conflict"].fp == 1
    assert m.per_detector["duplicate"].tp == 1
    assert m.per_category["positive"] == {"cases": 2, "passed": 1}
    assert m.per_category["negative"] == {"cases": 1, "passed": 1}
    assert m.per_category["ambiguous_negative"] == {"cases": 1, "passed": 1}


def test_fp_categories_are_labelled_by_case_kind():
    neg = _case("n", CaseCategory.NEGATIVE)
    suite = SuiteResult([_result(neg, fp=1)])
    m = compute_metrics(suite, [neg])
    assert m.fp_categories == {"negative:conflict": 1}
