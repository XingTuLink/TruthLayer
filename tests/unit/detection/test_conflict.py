"""ConflictDetector tests (#19), including key false-positive guards."""

from __future__ import annotations

from datetime import date

from truthlayer.detection.conflict import ConflictDetector
from truthlayer.domain.enums import DriftType

from .conftest import build_state, make_document, make_fact


def test_cross_document_overlapping_conflict(context):
    state = build_state(
        [
            make_fact(
                "old",
                value=149,
                document="price_2025.csv",
                observed_at=date(2025, 1, 1),
            ),
            make_fact(
                "new",
                value=299,
                document="price_2026.csv",
                observed_at=date(2026, 1, 1),
            ),
        ],
        documents=[
            make_document(
                "price_2025.csv", source_type="pricing", authority=0.9
            ),
            make_document(
                "price_2026.csv", source_type="pricing", authority=0.9
            ),
        ],
    )
    results = ConflictDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.CONFLICT
    assert drift.severity.value == "high"  # pricing_change override
    assert drift.confidence == 0.9
    assert drift.old_fact_id == make_fact("old").id
    assert drift.new_fact_id == make_fact("new").id
    assert drift.detail["old_value"] == 149
    assert drift.detail["new_value"] == 299
    assert drift.detail["old_source"] == "price_2025.csv"


def test_same_number_different_json_types_is_canonical(context):
    state = build_state(
        [
            make_fact("a", value=149, document="price_2025.csv"),
            make_fact("b", value=149.0, document="price_2026.csv"),
        ]
    )
    assert ConflictDetector().detect(state, context) == []


def test_disjoint_validity_windows_not_conflict(context):
    state = build_state(
        [
            make_fact(
                "a",
                value=149,
                document="price_2025.csv",
                valid_from=date(2025, 1, 1),
                valid_to=date(2025, 12, 31),
            ),
            make_fact(
                "b",
                value=299,
                document="price_2026.csv",
                valid_from=date(2026, 1, 1),
            ),
        ]
    )
    assert ConflictDetector().detect(state, context) == []


def test_same_document_tiered_values_not_conflict(context):
    # 年休假 5/10/15 天 — intentional tiers inside one policy document.
    state = build_state(
        [
            make_fact(
                f"tier{i}",
                subject="ACME 员工",
                predicate="年休假",
                value=days,
                document="vacation.txt",
            )
            for i, days in enumerate((5, 10, 15))
        ],
        documents=[make_document("vacation.txt")],
    )
    assert ConflictDetector().detect(state, context) == []


def test_explicit_supersession_not_conflict(context):
    state = build_state(
        [
            make_fact("old", value=149, document="price_2025.csv"),
            make_fact("new", value=299, document="price_2026.csv"),
        ],
        documents=[
            make_document("price_2025.csv", source_type="pricing"),
            make_document(
                "price_2026.csv",
                source_type="pricing",
                group="pricing",
                supersedes="price_2025.csv",
            ),
        ],
    )
    assert ConflictDetector().detect(state, context) == []


def test_multi_valued_predicate_escape_hatch(context):
    context.multi_valued_predicates  # sanity: frozenset attr exists
    state = build_state(
        [
            make_fact(
                "a", predicate="contact_phone", value="111",
                document="org1.txt",
            ),
            make_fact(
                "b", predicate="contact_phone", value="222",
                document="org2.txt",
            ),
        ],
        documents=[
            make_document("org1.txt"),
            make_document("org2.txt"),
        ],
    )
    # Default single-valued assumption -> conflict.
    assert len(ConflictDetector().detect(state, context)) == 1

    from truthlayer.detection.context import DetectionContext

    tolerant = DetectionContext(
        as_of=context.as_of,
        stale_after_days=365,
        pricing_stale_days=90,
        severity_overrides=dict(context.severity_overrides),
        multi_valued_predicates=frozenset(["contact_phone"]),
    )
    assert ConflictDetector().detect(state, tolerant) == []


def test_expired_fact_vs_undated_fact_not_conflict(context):
    # One source states an explicit validity end in the past; the other
    # current document carries no dates. The expired fact is a stale case,
    # not a current conflict.
    state = build_state(
        [
            make_fact(
                "expired",
                predicate="住宿标准",
                value=350,
                document="old_policy.csv",
                valid_from=date(2022, 3, 1),
                valid_to=date(2023, 12, 31),
            ),
            make_fact(
                "current",
                predicate="住宿标准",
                value=500,
                document="current_manual.csv",
            ),
        ],
        documents=[
            make_document("old_policy.csv"),
            make_document("current_manual.csv"),
        ],
    )
    assert ConflictDetector().detect(state, context) == []


def test_valid_to_equal_as_of_still_conflict(context):
    state = build_state(
        [
            make_fact(
                "a",
                predicate="住宿标准",
                value=350,
                document="old_policy.csv",
                valid_to=date(2026, 9, 25),
            ),
            make_fact(
                "b",
                predicate="住宿标准",
                value=500,
                document="current_manual.csv",
            ),
        ],
        documents=[
            make_document("old_policy.csv"),
            make_document("current_manual.csv"),
        ],
    )
    results = ConflictDetector().detect(state, context)
    assert len(results) == 1
    assert results[0].drift_type is DriftType.CONFLICT


def test_both_facts_expired_not_conflict(context):
    state = build_state(
        [
            make_fact(
                "a",
                predicate="住宿标准",
                value=300,
                document="old_a.csv",
                valid_from=date(2020, 1, 1),
                valid_to=date(2021, 12, 31),
            ),
            make_fact(
                "b",
                predicate="住宿标准",
                value=350,
                document="old_b.csv",
                valid_from=date(2021, 1, 1),
                valid_to=date(2022, 12, 31),
            ),
        ],
        documents=[
            make_document("old_a.csv"),
            make_document("old_b.csv"),
        ],
    )
    assert ConflictDetector().detect(state, context) == []


def test_different_predicates_or_subjects_not_conflict(context):
    state = build_state(
        [
            make_fact("a", predicate="list_price", value=149,
                      document="price_2025.csv"),
            make_fact("b", predicate="currency", value="CNY",
                      object_type="string", document="price_2026.csv"),
            make_fact(
                "c", subject="ACME CRM Max", value=399,
                document="price_2026.csv",
            ),
        ]
    )
    assert ConflictDetector().detect(state, context) == []
