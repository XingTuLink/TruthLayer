"""Golden QA regression — runs with no database and no LLM (#39, #40, #42).

This is the Phase 0 quality gate enforced on every test run: the committed
golden set must stay within the target size, cover all three case categories
and every detector, and the deterministic detection core must keep meeting
the Precision / Recall / F1 / FPR thresholds.
"""

from __future__ import annotations

from pathlib import Path

from truthlayer.domain.enums import DriftType
from truthlayer.evaluation.harness import run_suite
from truthlayer.evaluation.loader import load_cases
from truthlayer.evaluation.metrics import compute_metrics
from truthlayer.evaluation.schema import CaseCategory

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CASES_DIR = _REPO_ROOT / "examples" / "qa_cases"


def test_golden_set_size_and_coverage():
    cases = load_cases(_CASES_DIR)
    # Brief asks for 50–100 QA cases.
    assert 50 <= len(cases) <= 100

    categories = {case.category for case in cases}
    assert categories == set(CaseCategory)

    by_category = {kind: 0 for kind in CaseCategory}
    for case in cases:
        by_category[case.category] += 1
    assert by_category[CaseCategory.POSITIVE] >= 20
    assert by_category[CaseCategory.NEGATIVE] >= 10
    assert by_category[CaseCategory.AMBIGUOUS_NEGATIVE] >= 10

    # Every detector must have at least one labelled positive.
    expected_types = {
        expected.type
        for case in cases
        for expected in case.expected_drifts
    }
    assert expected_types == set(DriftType)


def test_golden_set_passes_every_case_and_phase0_gate():
    cases = load_cases(_CASES_DIR)
    suite = run_suite(cases)
    metrics = compute_metrics(suite, cases)

    assert suite.passed, [
        (r.case_id, r.false_positives, r.false_negatives)
        for r in suite.results
        if not r.passed
    ]
    assert metrics.gate.passed, metrics.gate.checks
    # All five detectors are perfect on the curated set by construction;
    # pin the finding-level counts so a regression cannot hide in an average.
    assert (metrics.tp, metrics.fp, metrics.fn) == (
        sum(len(c.expected_drifts) for c in cases),
        0,
        0,
    )
