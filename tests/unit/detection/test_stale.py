"""StaleDetector tests (#20)."""

from __future__ import annotations

from datetime import date

from truthlayer.detection.stale import StaleDetector
from truthlayer.domain.enums import DriftType, Severity

from .conftest import build_state, make_document, make_fact


def test_expired_valid_to_is_confirmed_stale(context):
    state = build_state(
        [
            make_fact(
                "f",
                document="policy.txt",
                valid_from=date(2020, 1, 1),
                valid_to=date(2025, 12, 31),
            )
        ],
        documents=[make_document("policy.txt")],
    )
    results = StaleDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.CONFIRMED_STALE
    assert drift.detail["reason"] == "valid_to_expired"
    assert drift.confidence == 0.95


def test_superseding_source_is_confirmed_stale(context):
    state = build_state(
        [make_fact("f", value=149, document="price_2025.csv")],
        documents=[
            make_document(
                "price_2025.csv",
                source_type="pricing",
                group="pricing",
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
    assert drift.severity is Severity.HIGH  # pricing_change override
    assert drift.new_document_id == make_document("price_2026.csv").id


def test_old_pricing_fact_without_replacement_is_possibly_stale(context):
    state = build_state(
        [
            make_fact(
                "f",
                value=149,
                document="price_2025.csv",
                observed_at=date(2025, 1, 1),
            )
        ],
        documents=[make_document("price_2025.csv", source_type="pricing")],
    )
    results = StaleDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.POSSIBLY_STALE
    assert drift.severity is Severity.WARNING
    assert drift.confidence == 0.6
    assert drift.detail["threshold_days"] == 90
    assert drift.detail["age_days"] > 90


def test_policy_uses_365_day_threshold(context):
    # 200 days old: stale for pricing (90) but fresh for policy (365).
    state = build_state(
        [
            make_fact(
                "f",
                predicate="quota",
                value="X",
                object_type="string",
                document="policy.txt",
                observed_at=date(2026, 3, 1),
            )
        ],
        documents=[make_document("policy.txt", source_type="policy")],
    )
    assert StaleDetector().detect(state, context) == []


def test_no_age_signal_never_stale(context):
    # Document old != knowledge old: no observed_at/valid_from => no claim.
    state = build_state(
        [make_fact("f", value="timeless", object_type="string",
                   document="policy.txt")],
        documents=[make_document("policy.txt")],
    )
    assert StaleDetector().detect(state, context) == []


def test_fresh_fact_not_stale(context):
    state = build_state(
        [
            make_fact(
                "f",
                document="price_2026.csv",
                observed_at=date(2026, 9, 1),
            )
        ],
        documents=[make_document("price_2026.csv", source_type="pricing")],
    )
    assert StaleDetector().detect(state, context) == []


def test_confirmed_takes_priority_over_possible(context):
    state = build_state(
        [
            make_fact(
                "f",
                value=149,
                document="price_2025.csv",
                observed_at=date(2024, 1, 1),
            )
        ],
        documents=[
            make_document("price_2025.csv", source_type="pricing"),
            make_document(
                "price_2026.csv",
                source_type="pricing",
                supersedes="price_2025.csv",
            ),
        ],
    )
    results = StaleDetector().detect(state, context)
    assert len(results) == 1
    assert results[0].drift_type is DriftType.CONFIRMED_STALE
