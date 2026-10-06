"""Phase 0 Gate metrics over a golden-suite run (#40).

Finding-level scores treat every expected drift as a labelled positive:

* TP — an expected finding produced by the right detector type;
* FP — a candidate the case did not expect;
* FN — an expected finding never produced.

The false-positive rate is measured at *case* level over the benign
(negative + ambiguous-negative) scenarios: ``false-alarm cases / benign
cases``. It answers the practical question "what fraction of normal or
merely suspicious-looking knowledge wrongly triggers a report", which is the
quantity a knowledge owner experiences. Both definitions are reported
transparently rather than conflated.

Thresholds use strict comparison per the brief: Precision > 80%,
Recall > 70%, F1 > 75%, FPR < 30%.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from truthlayer.evaluation.harness import SuiteResult
from truthlayer.evaluation.schema import CaseCategory, QACase

GATE_PRECISION = 0.80
GATE_RECALL = 0.70
GATE_F1 = 0.75
GATE_FPR = 0.30


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


@dataclass(frozen=True)
class DetectorMetric:
    expected: int
    tp: int
    fp: int
    fn: int

    @property
    def actual(self) -> int:
        return self.tp + self.fp

    @property
    def precision(self) -> float:
        return _rate(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float:
        return _rate(self.tp, self.tp + self.fn)


@dataclass(frozen=True)
class GateResult:
    checks: dict[str, bool]
    passed: bool

    @property
    def thresholds(self) -> dict[str, float]:
        return {
            "precision_gt": GATE_PRECISION,
            "recall_gt": GATE_RECALL,
            "f1_gt": GATE_F1,
            "fpr_lt": GATE_FPR,
        }


@dataclass(frozen=True)
class Metrics:
    total_cases: int
    passed_cases: int
    positive_cases: int
    benign_cases: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    false_alarm_cases: int
    fpr: float
    per_detector: dict[str, DetectorMetric]
    per_category: dict[str, dict[str, int]]
    #: (category, drift type) -> count of unexpected candidates
    fp_categories: dict[str, int]
    gate: GateResult

    def to_dict(self) -> dict:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "positive_cases": self.positive_cases,
            "benign_cases": self.benign_cases,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_alarm_cases": self.false_alarm_cases,
            "fpr": round(self.fpr, 4),
            "gate": {
                "passed": self.gate.passed,
                "checks": self.gate.checks,
                "thresholds": self.gate.thresholds,
            },
            "per_detector": {
                name: {
                    "expected": m.expected,
                    "actual": m.actual,
                    "tp": m.tp,
                    "fp": m.fp,
                    "fn": m.fn,
                    "precision": round(m.precision, 4),
                    "recall": round(m.recall, 4),
                }
                for name, m in sorted(self.per_detector.items())
            },
            "per_category": self.per_category,
            "fp_categories": dict(sorted(self.fp_categories.items())),
        }


def compute_metrics(suite: SuiteResult, cases: list[QACase]) -> Metrics:
    by_id = {case.id: case for case in cases}

    tp = sum(r.true_positives for r in suite.results)
    fp = sum(len(r.false_positives) for r in suite.results)
    fn = sum(len(r.false_negatives) for r in suite.results)

    precision = _rate(tp, tp + fp)
    recall = _rate(tp, tp + fn)
    f1 = _f1(precision, recall)

    # -- detector-level accounting ----------------------------------------
    expected_by_type: Counter[str] = Counter()
    fn_by_type: Counter[str] = Counter()
    fp_by_type: Counter[str] = Counter()

    for result in suite.results:
        case = by_id[result.case_id]
        types_by_origin = [e.type.value for e in case.expected_drifts]
        for type_name in types_by_origin:
            expected_by_type[type_name] += 1
        for origin in result.false_negatives:
            fn_by_type[types_by_origin[origin]] += 1
        for finding in result.false_positives:
            fp_by_type[finding["type"]] += 1

    all_types = set(expected_by_type) | set(fp_by_type)
    per_detector: dict[str, DetectorMetric] = {}
    for type_name in sorted(all_types):
        expected = expected_by_type[type_name]
        detector_fn = fn_by_type[type_name]
        detector_fp = fp_by_type[type_name]
        per_detector[type_name] = DetectorMetric(
            expected=expected,
            tp=expected - detector_fn,
            fp=detector_fp,
            fn=detector_fn,
        )

    # -- category accounting ----------------------------------------------
    per_category: dict[str, dict[str, int]] = {}
    for category in CaseCategory:
        rows = [r for r in suite.results if r.category is category]
        per_category[category.value] = {
            "cases": len(rows),
            "passed": sum(1 for r in rows if r.passed),
        }

    benign_categories = {CaseCategory.NEGATIVE, CaseCategory.AMBIGUOUS_NEGATIVE}
    benign_results = [r for r in suite.results if r.category in benign_categories]
    false_alarm_cases = sum(
        1 for r in benign_results if r.total_candidates > 0
    )
    fpr = _rate(false_alarm_cases, len(benign_results))

    fp_categories_counter: Counter[str] = Counter()
    for result in suite.results:
        for finding in result.false_positives:
            key = f"{result.category.value}:{finding['type']}"
            fp_categories_counter[key] += 1

    # -- Gate ---------------------------------------------------------------
    has_positives = tp + fn > 0
    has_benign = len(benign_results) > 0
    checks = {
        "non_empty_positive": has_positives,
        "non_empty_benign": has_benign,
        "precision_gt_0.80": precision > GATE_PRECISION,
        "recall_gt_0.70": recall > GATE_RECALL,
        "f1_gt_0.75": f1 > GATE_F1,
        "fpr_lt_0.30": fpr < GATE_FPR,
    }
    gate = GateResult(checks=checks, passed=all(checks.values()))

    return Metrics(
        total_cases=len(suite.results),
        passed_cases=sum(1 for r in suite.results if r.passed),
        positive_cases=per_category[CaseCategory.POSITIVE.value]["cases"],
        benign_cases=len(benign_results),
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        false_alarm_cases=false_alarm_cases,
        fpr=fpr,
        per_detector=per_detector,
        per_category=per_category,
        fp_categories=dict(fp_categories_counter),
        gate=gate,
    )
