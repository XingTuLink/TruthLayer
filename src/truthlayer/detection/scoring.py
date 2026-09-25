"""Shared severity / impact / confidence helpers for detectors."""

from __future__ import annotations

from datetime import date, datetime, timezone

from truthlayer.domain.enums import AIImpactLevel, Severity

#: Detector confidence floor; purely structural signals rank high,
#: age heuristics lower (kept explicit per detector).
STRUCTURAL_CONFIDENCE = 0.95
RECALL_CONFIDENCE = 0.90
HEURISTIC_CONFIDENCE = 0.60

_SEVERITY_TO_IMPACT: dict[Severity, AIImpactLevel] = {
    Severity.WARNING: AIImpactLevel.LOW,
    Severity.MEDIUM: AIImpactLevel.MEDIUM,
    Severity.HIGH: AIImpactLevel.HIGH,
    Severity.CRITICAL: AIImpactLevel.CRITICAL,
}


def ai_impact_for(severity: Severity) -> AIImpactLevel:
    return _SEVERITY_TO_IMPACT[severity]


def max_severity(left: Severity, right: Severity) -> Severity:
    from truthlayer.domain.enums import SEVERITY_RANK

    return left if SEVERITY_RANK[left] >= SEVERITY_RANK[right] else right


def at_utc_midday(day: date | None) -> datetime | None:
    """Place an effective date deterministically on the UTC timeline."""
    if day is None:
        return None
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc)
