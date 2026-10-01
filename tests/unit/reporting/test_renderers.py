"""JSON determinism and HTML autoescaping (#32, #56)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from truthlayer.reporting.dto import (
    CIBadge,
    EvidenceSnippet,
    FactSnippet,
    IssueReport,
    ReportDTO,
    ReportSummary,
    SourceSnippet,
)
from truthlayer.reporting.html_report import render_html
from truthlayer.reporting.json_report import render_json

XSS = "<script>alert('x')</script>"


def _issue(severity: str = "critical") -> IssueReport:
    return IssueReport(
        id=uuid.uuid4(),
        drift_type="conflict",
        severity=severity,
        status="open",
        detector_type="conflict_detector",
        ai_impact_level="critical",
        confidence=0.95,
        detected_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        subject=f"恶意实体{XSS}",
        predicate="座位分区",
        title="冲突标题-internal",
        human_title="人话标题",
        why="原因说明",
        recommendation="处置建议",
        suggested_decisions=["accept_newer", "keep_old"],
        old_fact=FactSnippet(
            fact_id=uuid.uuid4(),
            subject="旧主体",
            predicate="p",
            object_display="A",
            object_type="string",
            status="active",
            confidence=0.9,
            evidence=[
                EvidenceSnippet(
                    chunk_id=uuid.uuid4(),
                    quote=f"原文{XSS}",
                    source_type="policy",
                )
            ],
        ),
        new_fact=None,
        old_source=SourceSnippet(
            document_id=uuid.uuid4(),
            filename="old.md",
            source_type="policy",
            authority_score=0.9,
        ),
        new_source=None,
        evidence=[
            EvidenceSnippet(quote=f"证据{XSS}", source_type="policy")
        ],
    )


def _report(*, triggered: bool) -> ReportDTO:
    issue = _issue("critical" if triggered else "warning")
    return ReportDTO(
        summary=ReportSummary(
            workspace_id=uuid.uuid4(),
            workspace_name="demo-kb",
            generated_at=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
            tool_version="0.0.1",
            detector_version="drift-core-v1",
            documents_total=4,
            entities_total=26,
            facts_total=39,
            new_this_scan=1,
            open_total=1,
            ci=CIBadge(
                fail_on="critical" if triggered else "high",
                triggered=triggered,
                open_count=1,
                blocking_count=1 if triggered else 0,
                by_severity={"critical": 1}
                if triggered
                else {"warning": 1},
            ),
        ),
        issues=[issue],
    )


def test_json_is_deterministic_and_parseable() -> None:
    report = _report(triggered=True)
    first = render_json(report)
    second = render_json(report)
    assert first == second  # sorted keys, stable order

    payload = json.loads(first)
    assert payload["summary"]["workspace_name"] == "demo-kb"
    assert payload["summary"]["ci"]["triggered"] is True
    assert payload["issues"][0]["old_fact"]["evidence"][0]["quote"].startswith(
        "原文"
    )
    # Chinese must not be \u-escaped — reports are human-readable artifacts.
    assert "demo-kb" in first and "恶意实体" in first


def test_html_escapes_untrusted_model_text() -> None:
    html = render_html(_report(triggered=True))
    assert XSS not in html
    assert "&lt;script&gt;" in html
    assert "<title>" in html.lower()


def test_html_shows_ci_banner_and_sections() -> None:
    html = render_html(_report(triggered=True))
    assert "检查未通过" in html
    assert "退出码 1" in html
    # business-facing headline is the prominent card title
    assert "人话标题" in html
    # the raw internal title survives only inside the collapsed tech detail
    assert "原始标题" in html
    assert "相关文档原文" in html
    assert "处置建议" in html  # recommendation text passes through
    assert "truthlayer resolve" in html

    passing = render_html(_report(triggered=False))
    assert "检查通过" in passing


def test_html_renders_with_zero_issues() -> None:
    report = _report(triggered=False)
    report.issues = []
    html = render_html(report)
    assert "当前没有待确认的知识问题" in html


def test_html_handles_superseded_without_facts() -> None:
    issue = IssueReport(
        id=uuid.uuid4(),
        drift_type="superseded",
        severity="medium",
        status="open",
        detector_type="superseded_detector",
        ai_impact_level="medium",
        detected_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        title="旧文档已被取代",
        why="w",
        recommendation="r",
        old_source=SourceSnippet(
            document_id=uuid.uuid4(),
            filename="2025.md",
            source_type="policy",
            authority_score=0.9,
            version_label="2025",
        ),
        new_source=SourceSnippet(
            document_id=uuid.uuid4(),
            filename="2026.md",
            source_type="policy",
            authority_score=0.9,
            version_label="2026",
        ),
    )
    report = _report(triggered=False)
    report.issues = [issue]
    html = render_html(report)
    assert "2025.md" in html and "2026.md" in html


@pytest.mark.parametrize("triggered", [True, False])
def test_json_html_share_same_dto_fields(triggered: bool) -> None:
    report = _report(triggered=triggered)
    payload = json.loads(render_json(report))
    html = render_html(report)
    issue_id = payload["issues"][0]["id"]
    assert issue_id in html
    assert report.summary.detector_version in html
