"""StaleDetector mapping of valid_to provenance classes (fact-extract-v6).

quoted / legacy-None expiry  -> confirmed_stale (may block CI)
document_scope / calendar_derived expiry -> possibly_stale warning, but
always visible even with no age signal.
"""

from __future__ import annotations

from datetime import date

from truthlayer.detection.stale import StaleDetector
from truthlayer.domain.enums import DriftType, Severity

from .conftest import build_state, make_document, make_fact

_EXPIRED = date(2026, 9, 1)


def test_calendar_derived_expiry_is_visible_without_age_signal(context):
    # The run16 P0 shape: row fact has no observed_at / valid_from at all;
    # the downgraded expiry must still surface, never be silently dropped.
    state = build_state(
        [
            make_fact(
                "f",
                document="目录_2026Q3.xlsx",
                valid_to=_EXPIRED,
                valid_to_anchor="calendar_derived",
            )
        ],
        documents=[make_document("目录_2026Q3.xlsx", source_type="corporate")],
    )
    results = StaleDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.POSSIBLY_STALE
    assert drift.severity is Severity.WARNING
    assert drift.confidence == 0.6
    assert drift.detail["reason"] == "inferred_validity_expired"
    assert drift.detail["anchor_source"] == "calendar_derived"
    assert drift.detail["valid_to"] == "2026-09-01"


def test_document_scope_expiry_is_also_possibly(context):
    state = build_state(
        [
            make_fact(
                "f",
                document="差旅管理办法_2025版.pdf",
                valid_to=_EXPIRED,
                valid_to_anchor="document_scope",
            )
        ],
        documents=[make_document("差旅管理办法_2025版.pdf")],
    )
    results = StaleDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.POSSIBLY_STALE
    assert drift.detail["anchor_source"] == "document_scope"


def test_quoted_expiry_stays_confirmed(context):
    state = build_state(
        [
            make_fact(
                "f",
                document="promo.md",
                valid_to=_EXPIRED,
                valid_to_anchor="quoted",
            )
        ],
        documents=[make_document("promo.md")],
    )
    results = StaleDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.CONFIRMED_STALE
    assert drift.detail["reason"] == "valid_to_expired"
    assert drift.detail["valid_to_anchor"] == "quoted"


def test_legacy_null_anchor_expiry_stays_confirmed(context):
    # Pre-v6 rows carry no anchor tag; their historical behavior is trusted.
    state = build_state(
        [make_fact("f", document="old.txt", valid_to=_EXPIRED)],
        documents=[make_document("old.txt")],
    )
    results = StaleDetector().detect(state, context)

    assert len(results) == 1
    assert results[0].drift_type is DriftType.CONFIRMED_STALE
    assert results[0].detail["valid_to_anchor"] is None


def test_inferred_expiry_beats_fresh_observation(context):
    # Observed 5 days ago — age path alone would stay silent; the inferred
    # expiry still produces a review nudge.
    state = build_state(
        [
            make_fact(
                "f",
                document="季度促销页_2026Q3.md",
                observed_at=date(2026, 9, 20),
                valid_to=_EXPIRED,
                valid_to_anchor="calendar_derived",
            )
        ],
        documents=[
            make_document("季度促销页_2026Q3.md", source_type="pricing")
        ],
    )
    results = StaleDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.POSSIBLY_STALE
    assert drift.detail["reason"] == "inferred_validity_expired"


def test_inferred_anchor_not_yet_expired_stays_silent(context):
    state = build_state(
        [
            make_fact(
                "f",
                document="目录_2026Q4.xlsx",
                valid_to=date(2026, 12, 31),
                valid_to_anchor="calendar_derived",
            )
        ],
        documents=[make_document("目录_2026Q4.xlsx", source_type="corporate")],
    )
    assert StaleDetector().detect(state, context) == []


def test_inferred_expiry_with_explicit_supersession_stays_confirmed(context):
    # A deterministic supersession signal is independent of how valid_to is
    # anchored: explicit replacement still confirms, no downgrade.
    state = build_state(
        [
            make_fact(
                "f",
                document="price_2025.csv",
                valid_to=_EXPIRED,
                valid_to_anchor="calendar_derived",
            )
        ],
        documents=[
            make_document(
                "price_2025.csv", source_type="pricing", group="pricing"
            ),
            make_document(
                "price_2026.csv",
                source_type="pricing",
                group="pricing",
                supersedes="price_2025.csv",
            ),
        ],
    )
    results = StaleDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.CONFIRMED_STALE
    assert drift.detail["reason"] == "superseding_source"
