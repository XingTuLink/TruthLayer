"""Human- and machine-readable rendering of evaluation results."""

from __future__ import annotations

import json

from truthlayer.evaluation.harness import SuiteResult
from truthlayer.evaluation.metrics import Metrics
from truthlayer.evaluation.schema import ExpectedDrift, QACase

_CATEGORY_CN = {
    "positive": "应报(正例)",
    "negative": "正常(负例)",
    "ambiguous_negative": "疑似冲突(模糊负例)",
}


def _describe_expected(expected: ExpectedDrift) -> str:
    parts = [expected.type.value]
    if expected.subject:
        parts.append(f"subject={expected.subject}")
    if expected.predicate:
        parts.append(f"predicate={expected.predicate}")
    if expected.severity:
        parts.append(f"severity={expected.severity.value}")
    if expected.old_source:
        parts.append(f"old={expected.old_source}")
    if expected.new_source:
        parts.append(f"new={expected.new_source}")
    return " ".join(parts)


def _describe_finding(finding: dict) -> str:
    parts = [
        f"{finding['type']}({finding['severity']})",
        f"detector={finding['detector']}",
    ]
    if finding.get("subject"):
        parts.append(f"subject={finding['subject']}")
    if finding.get("predicate"):
        parts.append(f"predicate={finding['predicate']}")
    if finding.get("old_source") or finding.get("new_source"):
        parts.append(
            f"{finding.get('old_source') or '?'} -> {finding.get('new_source') or '?'}"
        )
    return " ".join(parts)


def _failures(suite: SuiteResult, cases: list[QACase]) -> list[dict]:
    by_id = {case.id: case for case in cases}
    failures = []
    for result in suite.results:
        if result.passed:
            continue
        case = by_id[result.case_id]
        failures.append(
            {
                "case_id": result.case_id,
                "title": result.title,
                "category": result.category.value,
                "missing": [
                    _describe_expected(case.expected_drifts[i])
                    for i in result.false_negatives
                ],
                "unexpected": [
                    _describe_finding(f) for f in result.false_positives
                ],
            }
        )
    return failures


def render_json(metrics: Metrics, suite: SuiteResult, cases: list[QACase]) -> str:
    payload = metrics.to_dict()
    payload["failures"] = _failures(suite, cases)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def render_text(metrics: Metrics, suite: SuiteResult, cases: list[QACase]) -> str:
    gate = metrics.gate
    lines: list[str] = []
    lines.append("TruthLayer 知识漂移检测 · Golden Set 评测")
    lines.append("=" * 52)
    lines.append(
        f"用例 {metrics.total_cases}（通过 {metrics.passed_cases}）· "
        f"正例 {metrics.positive_cases} · 良性负例 {metrics.benign_cases}"
    )
    lines.append("")
    lines.append("Finding 级指标（期望漂移为标注正例）")
    lines.append(f"  TP={metrics.tp}  FP={metrics.fp}  FN={metrics.fn}")
    lines.append(
        f"  Precision = {metrics.precision:.1%}   (Gate > 80%)  "
        f"{'PASS' if gate.checks['precision_gt_0.80'] else 'FAIL'}"
    )
    lines.append(
        f"  Recall    = {metrics.recall:.1%}   (Gate > 70%)  "
        f"{'PASS' if gate.checks['recall_gt_0.70'] else 'FAIL'}"
    )
    lines.append(
        f"  F1        = {metrics.f1:.1%}   (Gate > 75%)  "
        f"{'PASS' if gate.checks['f1_gt_0.75'] else 'FAIL'}"
    )
    lines.append(
        f"  FPR(良性场景误报) = {metrics.fpr:.1%} "
        f"({metrics.false_alarm_cases}/{metrics.benign_cases})  (Gate < 30%)  "
        f"{'PASS' if gate.checks['fpr_lt_0.30'] else 'FAIL'}"
    )
    lines.append("")
    lines.append("检测器级 Precision / Recall")
    for name, m in metrics.per_detector.items():
        lines.append(
            f"  {name:<16} expected={m.expected:<3} tp={m.tp:<3} "
            f"fp={m.fp:<3} fn={m.fn:<3} P={m.precision:.0%} R={m.recall:.0%}"
        )
    lines.append("")
    lines.append("分类通过情况")
    for category, stats in metrics.per_category.items():
        lines.append(
            f"  {_CATEGORY_CN.get(category, category):<18} "
            f"{stats['passed']}/{stats['cases']}"
        )

    failures = _failures(suite, cases)
    if failures:
        lines.append("")
        lines.append(f"未通过用例（{len(failures)}）")
        for failure in failures:
            lines.append(
                f"  [{failure['category']}] {failure['case_id']} — {failure['title']}"
            )
            for missing in failure["missing"]:
                lines.append(f"      缺失: {missing}")
            for unexpected in failure["unexpected"]:
                lines.append(f"      误报: {unexpected}")

    lines.append("")
    lines.append(
        "Phase 0 Gate: " + ("PASS ✅" if gate.passed else "FAIL ❌")
    )
    return "\n".join(lines)


def render_markdown(metrics: Metrics, suite: SuiteResult, cases: list[QACase]) -> str:
    gate = metrics.gate
    lines: list[str] = []
    lines.append("# TruthLayer Golden Set 评测结果")
    lines.append("")
    lines.append(
        f"**Phase 0 Gate：{'PASS' if gate.passed else 'FAIL'}** · "
        f"用例 {metrics.total_cases}（通过 {metrics.passed_cases}）· "
        f"正例 {metrics.positive_cases} · 良性负例 {metrics.benign_cases}"
    )
    lines.append("")
    lines.append("| 指标 | 值 | Gate | 结果 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| Precision | {metrics.precision:.1%} | > 80% | "
        f"{'✅' if gate.checks['precision_gt_0.80'] else '❌'} |"
    )
    lines.append(
        f"| Recall | {metrics.recall:.1%} | > 70% | "
        f"{'✅' if gate.checks['recall_gt_0.70'] else '❌'} |"
    )
    lines.append(
        f"| F1 | {metrics.f1:.1%} | > 75% | "
        f"{'✅' if gate.checks['f1_gt_0.75'] else '❌'} |"
    )
    lines.append(
        f"| FPR（良性场景误报） | {metrics.fpr:.1%} | < 30% | "
        f"{'✅' if gate.checks['fpr_lt_0.30'] else '❌'} |"
    )
    lines.append("")
    lines.append(f"计数：TP={metrics.tp} · FP={metrics.fp} · FN={metrics.fn}")
    lines.append("")
    lines.append("## 检测器级")
    lines.append("")
    lines.append("| 检测器 | expected | TP | FP | FN | Precision | Recall |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, m in metrics.per_detector.items():
        lines.append(
            f"| {name} | {m.expected} | {m.tp} | {m.fp} | {m.fn} "
            f"| {m.precision:.0%} | {m.recall:.0%} |"
        )
    lines.append("")

    failures = _failures(suite, cases)
    if failures:
        lines.append("## 未通过用例")
        lines.append("")
        for failure in failures:
            lines.append(
                f"- **{failure['case_id']}** ({failure['category']}) — "
                f"{failure['title']}"
            )
            for missing in failure["missing"]:
                lines.append(f"  - 缺失：`{missing}`")
            for unexpected in failure["unexpected"]:
                lines.append(f"  - 误报：`{unexpected}`")
        lines.append("")
    return "\n".join(lines)
