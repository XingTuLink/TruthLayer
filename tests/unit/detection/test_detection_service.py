"""Unit tests for DriftDetectionService JSONB safety.

Regression (run8 / fact-extract-v5): KnowledgeState restores date-typed fact
values to real ``datetime.date`` objects for window comparisons; detectors
(duplicate's shared ``object``, conflict's old/new values) embed those values
verbatim in ``candidate.detail``. The single JSONB exit must coerce them, or
psycopg raises ``TypeError: Object of type date is not JSON serializable``
when persisting the drift.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from truthlayer.detection.service import _json_safe


def test_json_safe_converts_nested_dates() -> None:
    payload = {
        "object": date(2026, 7, 1),
        "nested": {"when": datetime(2026, 7, 1, 9, 30)},
        "dates": [date(2025, 1, 1), date(2026, 1, 1)],
        "plain": ("x", 1, 2.5, True, None),
    }

    result = _json_safe(payload)

    assert result["object"] == "2026-07-01"
    assert result["nested"]["when"] == "2026-07-01T09:30:00"
    assert result["dates"] == ["2025-01-01", "2026-01-01"]
    assert list(result["plain"]) == ["x", 1, 2.5, True, None]
    # The whole point: the coerced payload must round-trip through the JSON
    # encoder psycopg uses.
    json.dumps(result)


def test_json_safe_passes_through_scalars() -> None:
    assert _json_safe("22800") == "22800"
    assert _json_safe(22800) == 22800
    assert _json_safe(None) is None
