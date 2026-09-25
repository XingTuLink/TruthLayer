"""Tests for shared deterministic comparison helpers."""

from __future__ import annotations

import uuid
from datetime import date

from truthlayer.detection.candidates import (
    canonical_object,
    objects_equal,
    windows_overlap,
)


def test_windows_overlap_rules():
    d = date(2025, 1, 1)
    assert windows_overlap(None, None, None, None)
    assert windows_overlap(d, None, date(2026, 1, 1), None)
    assert windows_overlap(
        date(2025, 1, 1), date(2025, 6, 1),
        date(2025, 5, 1), date(2026, 1, 1),
    )
    assert not windows_overlap(
        date(2025, 1, 1), date(2025, 6, 1),
        date(2025, 7, 1), date(2026, 1, 1),
    )
    # Touching at a boundary still overlaps inclusively.
    assert windows_overlap(
        date(2025, 1, 1), date(2025, 6, 1),
        date(2025, 6, 1), date(2026, 1, 1),
    )


def test_canonical_number_and_string_normalization():
    assert canonical_object(149, "number", None) == canonical_object(
        149.0, "number", None
    )
    assert canonical_object(True, "boolean", None) == (
        "scalar", "boolean", True
    )
    assert canonical_object(" CNY ", "string", None) == (
        "scalar", "string", "CNY"
    )
    eid = uuid.uuid4()
    assert canonical_object(None, None, eid) == ("entity", str(eid))


def test_objects_equal():
    assert objects_equal(149, "number", None, 149.0, "number", None)
    assert not objects_equal(149, "number", None, "149", "string", None)
    a, b = uuid.uuid4(), uuid.uuid4()
    assert not objects_equal(None, None, a, None, None, b)
    assert objects_equal(None, None, a, None, None, a)
