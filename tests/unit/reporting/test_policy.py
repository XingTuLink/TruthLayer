"""CI fail_on threshold semantics (#26)."""

from __future__ import annotations

from collections import Counter

from truthlayer.domain.enums import CIFailOn
from truthlayer.reporting.policy import count_blocking, severity_triggers_fail_on


def test_none_never_triggers() -> None:
    assert not severity_triggers_fail_on("critical", CIFailOn.NONE)
    assert count_blocking(["critical", "warning"], CIFailOn.NONE) == 0


def test_threshold_is_inclusive() -> None:
    assert severity_triggers_fail_on("critical", CIFailOn.CRITICAL)
    assert not severity_triggers_fail_on("high", CIFailOn.CRITICAL)
    assert severity_triggers_fail_on("high", CIFailOn.HIGH)
    assert severity_triggers_fail_on("critical", CIFailOn.HIGH)


def test_warning_threshold_catches_everything() -> None:
    severities = ["warning", "medium", "high", "critical"]
    assert count_blocking(severities, CIFailOn.WARNING) == 4


def test_count_blocking_matrix() -> None:
    severities = ["warning", "medium", "high", "critical", "high"]
    assert count_blocking(severities, CIFailOn.CRITICAL) == 1
    assert count_blocking(severities, CIFailOn.HIGH) == 3
    assert count_blocking(severities, CIFailOn.MEDIUM) == 4
    assert count_blocking(severities, CIFailOn.WARNING) == 5
    assert count_blocking([], CIFailOn.HIGH) == 0


def test_accepts_counter_directly() -> None:
    counter = Counter({"high": 2, "critical": 1})
    assert count_blocking(counter, CIFailOn.HIGH) == 3
