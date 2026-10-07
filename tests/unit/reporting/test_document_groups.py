"""R11 — document-level rollup of fact-level stale storms (pure report layer).

``ReportBuilder._roll_up_document_stale`` is a static, DB-free function over
assembled IssueReport DTOs: detection rows and CI counts keep fact
granularity, only the presentation collapses same-document same-type
findings (>=2 members) into one document-level card with members embedded.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from truthlayer.reporting.builder import ReportBuilder
from truthlayer.reporting.dto import (
    CIBadge,
    IssueReport,
    ReportDTO,
    ReportSummary,
    SourceSnippet,
)
from truthlayer.reporting.html_report import render_html
from truthlayer.reporting.json_report import render_json

_DETECTED = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)


def _source(name: str) -> SourceSnippet:
    return SourceSnippet(
        document_id=uuid.uuid5(uuid.NAMESPACE_URL, f"doc:{name}"),
        filename=name,
        source_type="corporate",
        authority_score=0.9,
    )


def _stale_issue(
    fid: str,
    *,
    drift_type: str = "confirmed_stale",
    severity: str = "medium",
    source: SourceSnippet | None = None,
    predicate: str = "list_price",
) -> IssueReport:
    source = source or _source("员工手册_2023.pdf")
    return IssueReport(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"drift:{fid}"),
        drift_type=drift_type,
        severity=severity,
        status="open",
        detector_type="stale_detector",
        ai_impact_level=severity,
        confidence=0.9,
        detected_at=_DETECTED,
        subject="云客服专业版",
        predicate=predicate,
        title="内部标题",
        why="原因",
        recommendation="建议",
        suggested_decisions=["accept_newer", "false_positive"],
        old_source=source,
    )


def _rollup(issues):
    return ReportBuilder._roll_up_document_stale(list(issues))


def test_collapses_same_doc_confirmed_stale_into_one_group():
    handbook = _source("员工手册_2023.pdf")
    issues = [
        _stale_issue("a", source=handbook, predicate="年假天数"),
        _stale_issue("b", source=handbook, severity="high", predicate="住宿标准"),
        _stale_issue("c", source=handbook, predicate="加班补贴"),
    ]

    ungrouped, groups = _rollup(issues)

    assert ungrouped == []
    assert len(groups) == 1
    g = groups[0]
    assert g.drift_type == "confirmed_stale"
    assert g.count == 3
    # Highest member severity wins so the document card sorts correctly.
    assert g.severity == "high"
    assert g.document_id == handbook.document_id
    assert g.filename == "员工手册_2023.pdf"
    assert {mi.id for mi in g.issues} == {i.id for i in issues}
    assert "员工手册_2023.pdf" in g.title and "3 条" in g.title
    assert g.why and g.recommendation
    assert "false_positive" in g.suggested_decisions
    assert g.first_detected_at == _DETECTED


def test_possibly_stale_rolls_up_independently():
    sla = _source("SLA服务协议_2021.pdf")
    issues = [
        _stale_issue("p1", drift_type="possibly_stale", severity="warning",
                     source=sla, predicate="响应时效"),
        _stale_issue("p2", drift_type="possibly_stale", severity="warning",
                     source=sla, predicate="工单流程"),
    ]

    ungrouped, groups = _rollup(issues)

    assert ungrouped == []
    assert len(groups) == 1
    assert groups[0].drift_type == "possibly_stale"
    assert groups[0].severity == "warning"
    # Neutral wording: a possibly_stale group may mix age-only facts and
    # facts whose expiry rests on a document-scope / inferred date.
    assert "时效性存疑" in groups[0].title


def test_single_fact_stays_a_standalone_issue():
    issue = _stale_issue("solo")

    ungrouped, groups = _rollup([issue])

    assert groups == []
    assert [i.id for i in ungrouped] == [issue.id]


def test_different_documents_form_separate_groups():
    issues = [
        _stale_issue("a1", source=_source("手册A.pdf")),
        _stale_issue("a2", source=_source("手册A.pdf")),
        _stale_issue("b1", source=_source("手册B.pdf"), severity="high"),
        _stale_issue("b2", source=_source("手册B.pdf"), severity="high"),
    ]

    ungrouped, groups = _rollup(issues)

    assert ungrouped == []
    assert {g.filename for g in groups} == {"手册A.pdf", "手册B.pdf"}
    # High-severity document sorts before medium.
    assert groups[0].filename == "手册B.pdf"
    assert all(g.count == 2 for g in groups)


def test_non_stale_and_r16_issues_are_not_grouped():
    doc = _source("价目表_2025.xlsx")
    issues = [
        IssueReport(
            id=uuid.uuid4(), drift_type="conflict", severity="high",
            status="open", detector_type="conflict_detector",
            ai_impact_level="high", detected_at=_DETECTED,
            title="t", why="w", recommendation="r", old_source=doc,
        ),
        IssueReport(
            id=uuid.uuid4(), drift_type="reused_stale_value",
            severity="high", status="open",
            detector_type="stale_detector", ai_impact_level="high",
            detected_at=_DETECTED, title="t", why="w",
            recommendation="r", old_source=doc,
        ),
    ]

    ungrouped, groups = _rollup(issues)

    assert groups == []
    assert len(ungrouped) == 2


def test_stale_without_document_anchor_stays_standalone():
    issue = _stale_issue("no-doc")
    issue2 = _stale_issue("no-doc2")
    issue.old_source = None
    issue2.old_source = None

    ungrouped, groups = _rollup([issue, issue2])

    assert groups == []
    assert len(ungrouped) == 2


def _report(groups, issues) -> ReportDTO:
    return ReportDTO(
        summary=ReportSummary(
            workspace_id=uuid.uuid4(),
            workspace_name="demo-kb",
            generated_at=_DETECTED,
            tool_version="0.0.1",
            detector_version="drift-core-v3",
            open_total=3,
            open_groups_total=len(groups),
            ci=CIBadge(
                fail_on="high", triggered=True, open_count=3,
                blocking_count=3, by_severity={"medium": 3},
            ),
        ),
        issues=issues,
        groups=groups,
    )


def test_groups_render_in_html_with_collapsed_member_ids():
    issues = [_stale_issue(f"m{i}", predicate=f"条款{i}") for i in range(3)]
    _, groups = _rollup(issues)
    html = render_html(_report(groups, []))

    assert "文档级" in html
    assert "员工手册_2023.pdf" in html
    assert "3 条事实" in html
    assert "事实明细" in html
    # Every member remains drill-down resolvable from the report.
    for issue in issues:
        assert str(issue.id) in html


def test_groups_serialize_in_json_report():
    issues = [_stale_issue(f"j{i}") for i in range(2)]
    _, groups = _rollup(issues)
    payload = json.loads(render_json(_report(groups, [])))

    assert payload["summary"]["open_groups_total"] == 1
    raw = payload["groups"][0]
    assert raw["group_type"] == "document_stale"
    assert raw["drift_type"] == "confirmed_stale"
    assert raw["count"] == 2
    assert len(raw["issues"]) == 2
