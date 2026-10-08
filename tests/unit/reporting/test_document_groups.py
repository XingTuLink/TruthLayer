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
    EvidenceSnippet,
    IssueReport,
    ReportDTO,
    ReportSummary,
    SourceSnippet,
)
from truthlayer.reporting.humanize import narrative, plain_title, short_title
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


# -- self-declared-expiry document heads ------------------------------------

_EXPIRY_QUOTE = "本标准有效期至2023年12月31日，到期自行废止。"


def _declared_head(
    fid: str = "head",
    *,
    source: SourceSnippet | None = None,
) -> IssueReport:
    source = source or _source("差旅标准_2022.pdf")
    issue = _stale_issue(
        fid,
        source=source,
        predicate="有效期至",
    )
    issue.subject = "差旅标准（2022版）"
    issue.detail = {
        "reason": "document_self_declared_expired",
        "valid_to": "2023-12-31",
        "valid_to_anchor": "quoted",
        "old_source": source.filename,
        "source": source.filename,
        "subject": "差旅标准（2022版）",
        "predicate": "有效期至",
    }
    issue.title = short_title("confirmed_stale", issue.detail)
    issue.human_title = plain_title("confirmed_stale", issue.detail)
    story = narrative("confirmed_stale", issue.detail)
    issue.why = story.why
    issue.recommendation = story.recommendation
    issue.suggested_decisions = list(story.suggested_decisions)
    issue.evidence = [
        EvidenceSnippet(quote=_EXPIRY_QUOTE, filename=source.filename)
    ]
    return issue


def test_declared_head_absorbs_mixed_stale_members():
    doc = _source("差旅标准_2022.pdf")
    head = _declared_head(source=doc)
    members = [
        _stale_issue(
            "p1",
            drift_type="possibly_stale",
            severity="warning",
            source=doc,
            predicate="住宿标准",
        ),
        _stale_issue("c1", source=doc, predicate="交通补贴"),
    ]

    ungrouped, groups = _rollup([head, *members])

    assert ungrouped == []
    assert len(groups) == 1
    g = groups[0]
    assert g.group_type == "document_self_declared_expired"
    assert g.head_reason == "document_self_declared_expired"
    assert g.drift_type == "confirmed_stale"
    assert g.severity == "medium"
    assert g.count == 3
    assert {mi.id for mi in g.issues} == {head.id, *(m.id for m in members)}
    assert "差旅标准_2022.pdf" in g.title
    assert "2023-12-31" in g.title
    # No successor to accept; only acknowledge/keep or dismiss.
    assert g.suggested_decisions == ["keep_old", "false_positive"]
    assert g.evidence[0].quote == _EXPIRY_QUOTE


def test_declared_group_forms_with_single_head_only():
    # The min-2 R11 threshold does not apply to a self-declared head.
    head = _declared_head()

    ungrouped, groups = _rollup([head])

    assert ungrouped == []
    assert len(groups) == 1
    assert groups[0].count == 1
    assert groups[0].issues[0].id == head.id


def test_declared_head_does_not_swallow_other_documents_groups():
    expired_doc = _source("差旅标准_2022.pdf")
    other_doc = _source("SLA服务协议_2021.pdf")
    head = _declared_head(source=expired_doc)
    own_member = _stale_issue(
        "own",
        drift_type="possibly_stale",
        severity="warning",
        source=expired_doc,
    )
    others = [
        _stale_issue(
            f"s{i}",
            drift_type="possibly_stale",
            severity="warning",
            source=other_doc,
        )
        for i in range(2)
    ]

    ungrouped, groups = _rollup([head, own_member, *others])

    assert ungrouped == []
    assert len(groups) == 2
    by_doc = {g.document_id: g for g in groups}
    declared_group = by_doc[expired_doc.document_id]
    assert declared_group.group_type == "document_self_declared_expired"
    assert {mi.id for mi in declared_group.issues} == {
        head.id,
        own_member.id,
    }
    other_group = by_doc[other_doc.document_id]
    assert other_group.group_type == "document_stale"
    assert {mi.id for mi in other_group.issues} == {m.id for m in others}


def test_declared_group_renders_statement_quote_in_html():
    head = _declared_head()
    member = _stale_issue(
        "m1",
        drift_type="possibly_stale",
        severity="warning",
        source=head.old_source,
        predicate="住宿标准",
    )
    _, groups = _rollup([head, member])
    html = render_html(_report(groups, []))

    assert "文档失效声明原文" in html
    assert _EXPIRY_QUOTE in html
    assert "文档声明" in html
    assert "2023-12-31" in html
    assert str(head.id) in html and str(member.id) in html


def test_declared_group_serializes_in_json_report():
    head = _declared_head()
    _, groups = _rollup([head])
    payload = json.loads(render_json(_report(groups, [])))

    raw = payload["groups"][0]
    assert raw["group_type"] == "document_self_declared_expired"
    assert raw["head_reason"] == "document_self_declared_expired"
    assert raw["evidence"][0]["quote"] == _EXPIRY_QUOTE
    assert raw["suggested_decisions"] == ["keep_old", "false_positive"]
