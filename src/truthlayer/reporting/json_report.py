"""Deterministic JSON rendering of ReportDTO (#56)."""

from __future__ import annotations

import json

from truthlayer.reporting.dto import ReportDTO


def render_json(report: ReportDTO) -> str:
    """Stable serialization: sorted keys, UTC ISO datetimes, unescaped UTF-8."""
    payload = report.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
    )
