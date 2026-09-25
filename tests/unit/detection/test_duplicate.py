"""DuplicateDetector tests (#21, #12)."""

from __future__ import annotations

from datetime import date

from truthlayer.detection.duplicate import DuplicateDetector, cosine_similarity
from truthlayer.domain.enums import DriftType

from .conftest import build_state, make_document, make_entity, make_fact


def test_name_containment_recalls_duplicate(context):
    state = build_state(
        [
            make_fact("a", subject="ACME CRM", value=149,
                      document="price_2025.csv"),
            make_fact("b", subject="ACME CRM Pro", value=149,
                      document="price_2026.csv"),
        ]
    )
    results = DuplicateDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.DUPLICATE
    assert drift.detail["recall"] == "name_token_containment"
    assert drift.confidence == 0.9
    assert drift.detail["entity_a"] == "ACME CRM"
    assert drift.detail["entity_b"] == "ACME CRM Pro"


def test_exact_normalized_name_confirms_structurally(context):
    state = build_state(
        [
            make_fact("a", subject="acme crm", value="CNY",
                      object_type="string"),
            make_fact("b", subject="ACME  CRM", value="CNY",
                      object_type="string"),
        ]
    )
    results = DuplicateDetector().detect(state, context)
    assert len(results) == 1
    assert results[0].detail["recall"] == "exact_normalized_name"
    assert results[0].confidence == 0.95


def test_embedding_recall_then_deterministic_confirm(context):
    state = build_state(
        [
            make_fact("a", subject="星辰云主机", value=99,
                      document="d1.txt"),
            make_fact("b", subject="星辰云主机尊享版", value=99,
                      document="d2.txt"),
        ],
        documents=[
            make_document("d1.txt"),
            make_document("d2.txt"),
        ],
        extra_entities=[
            make_entity("星辰云主机", embedding=(1.0, 0.0, 0.0)),
            make_entity("星辰云主机尊享版", embedding=(0.99, 0.14, 0.0)),
        ],
    )
    results = DuplicateDetector().detect(state, context)
    assert len(results) == 1
    assert results[0].detail["recall"] == "embedding_similarity"


def test_similar_names_different_values_not_duplicate(context):
    state = build_state(
        [
            make_fact("a", subject="ACME CRM", value=149,
                      document="price_2025.csv"),
            make_fact("b", subject="ACME CRM Pro", value=399,
                      document="price_2026.csv"),
        ]
    )
    assert DuplicateDetector().detect(state, context) == []


def test_non_overlapping_windows_not_duplicate(context):
    state = build_state(
        [
            make_fact(
                "a", subject="ACME CRM", value=149,
                valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31),
            ),
            make_fact(
                "b", subject="ACME CRM Pro", value=149,
                valid_from=date(2026, 1, 1),
            ),
        ]
    )
    assert DuplicateDetector().detect(state, context) == []


def test_unrelated_names_no_embeddings_not_recalled(context):
    state = build_state(
        [
            make_fact("a", subject="某差旅平台", value="高铁二等座",
                      object_type="string"),
            make_fact("b", subject="ACME CRM Pro", value="高铁二等座",
                      object_type="string"),
        ]
    )
    assert DuplicateDetector().detect(state, context) == []


def test_single_generic_fact_shared_by_three_entities_not_duplicate(context):
    # "unit = 座席/月" alone must not merge product SKUs.
    state = build_state(
        [
            make_fact("h", subject="ACME Helpdesk", value="座席/月",
                      predicate="unit", object_type="string",
                      document="h1.txt"),
            make_fact("hp", subject="ACME Helpdesk Pro", value="座席/月",
                      predicate="unit", object_type="string",
                      document="h2.txt"),
            make_fact("hs", subject="ACME Helpdesk Standard", value="座席/月",
                      predicate="unit", object_type="string",
                      document="h3.txt"),
        ],
        documents=[
            make_document("h1.txt"),
            make_document("h2.txt"),
            make_document("h3.txt"),
        ],
    )
    assert DuplicateDetector().detect(state, context) == []


def test_single_identifying_fact_confirms_duplicate(context):
    # Same price held by exactly two name-related entities is identifying.
    state = build_state(
        [
            make_fact("a", subject="ACME CRM", value=149,
                      predicate="list_price", document="d1.txt"),
            make_fact("b", subject="ACME CRM Pro", value=149,
                      predicate="list_price", document="d2.txt"),
        ],
        documents=[
            make_document("d1.txt"),
            make_document("d2.txt"),
        ],
    )
    results = DuplicateDetector().detect(state, context)
    assert len(results) == 1
    assert results[0].detail["shared_fact_count"] == 1


def test_two_shared_facts_confirm_even_if_generic(context):
    state = build_state(
        [
            make_fact("a1", subject="ACME CRM", value="用户/月",
                      predicate="unit", object_type="string",
                      document="d1.txt"),
            make_fact("a2", subject="ACME CRM", value="CNY",
                      predicate="currency", object_type="string",
                      document="d1.txt"),
            make_fact("b1", subject="ACME CRM Pro", value="用户/月",
                      predicate="unit", object_type="string",
                      document="d2.txt"),
            make_fact("b2", subject="ACME CRM Pro", value="CNY",
                      predicate="currency", object_type="string",
                      document="d2.txt"),
        ],
        documents=[
            make_document("d1.txt"),
            make_document("d2.txt"),
        ],
    )
    results = DuplicateDetector().detect(state, context)
    assert len(results) == 2
    assert all(r.detail["shared_fact_count"] == 2 for r in results)


def test_same_subject_canonical_fact_not_duplicate(context):
    # Extraction already merges identical rows; one entity => no pair.
    state = build_state(
        [
            make_fact("a", subject="ACME CRM Pro", value=149,
                      document="price_2025.csv"),
        ]
    )
    assert DuplicateDetector().detect(state, context) == []


def test_cosine_helper():
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == 1.0
    assert cosine_similarity((1.0, 0.0), (0.0, 1.0)) == 0.0
    assert cosine_similarity((1.0,), (1.0, 2.0)) == 0.0
