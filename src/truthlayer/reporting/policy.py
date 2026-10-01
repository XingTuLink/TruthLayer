"""CI fail_on policy — pure function, stable exit-code semantics (#26).

0 = pass, 1 = failed according to fail_on, 2 = system/runtime error.
Only OPEN drifts count: once a human ignores or resolves an issue it must
not keep breaking the build.
"""

from __future__ import annotations

from collections import Counter

from truthlayer.domain.enums import (
    CIFailOn,
    FAIL_ON_RANK,
    SEVERITY_RANK,
    Severity,
)


def severity_triggers_fail_on(severity: str, fail_on: CIFailOn) -> bool:
    """True when an open drift of ``severity`` meets the threshold."""
    if fail_on is CIFailOn.NONE:
        return False
    return SEVERITY_RANK[Severity(severity)] >= FAIL_ON_RANK[fail_on]


def count_blocking(
    open_severities: list[str] | Counter,
    fail_on: CIFailOn,
) -> int:
    """How many open drifts meet or exceed the configured threshold."""
    if fail_on is CIFailOn.NONE:
        return 0
    threshold = FAIL_ON_RANK[fail_on]
    if isinstance(open_severities, Counter):
        counter = open_severities
    else:
        counter = Counter(open_severities)
    return sum(
        count
        for value, count in counter.items()
        if SEVERITY_RANK[Severity(value)] >= threshold
    )
