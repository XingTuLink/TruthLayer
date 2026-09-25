"""SupersededDetector tests (#8, #18)."""

from __future__ import annotations

from truthlayer.detection.superseded import SupersededDetector
from truthlayer.domain.enums import DriftType, Severity, TargetType

from .conftest import build_state, make_document, make_fact, uid


def test_explicit_chain_emits_one_document_drift(context):
    state = build_state(
        [make_fact("f", document="price_2025.csv")],
        documents=[
            make_document("price_2025.csv", source_type="pricing",
                          group="pricing"),
            make_document(
                "price_2026.csv",
                source_type="pricing",
                group="pricing",
                supersedes="price_2025.csv",
            ),
        ],
    )
    results = SupersededDetector().detect(state, context)

    assert len(results) == 1
    drift = results[0]
    assert drift.drift_type is DriftType.SUPERSEDED
    assert drift.target_type is TargetType.DOCUMENT
    assert drift.target_id == uid("doc:price_2025.csv")
    assert drift.old_document_id == uid("doc:price_2025.csv")
    assert drift.new_document_id == uid("doc:price_2026.csv")
    assert drift.severity is Severity.HIGH
    assert drift.detail["reason"] == "explicit_version_replacement"


def test_no_chain_no_drift(context):
    state = build_state(
        [make_fact("f")],
        documents=[
            make_document("price_2025.csv"),
            make_document("vacation.txt"),
        ],
    )
    assert SupersededDetector().detect(state, context) == []


def test_three_version_chain_emits_two_drifts(context):
    state = build_state(
        [],
        documents=[
            make_document("p2024.csv", group="p", supersedes=None),
            make_document("p2025.csv", group="p", supersedes="p2024.csv"),
            make_document("p2026.csv", group="p", supersedes="p2025.csv"),
        ],
    )
    results = SupersededDetector().detect(state, context)

    targets = {(d.old_document_id, d.new_document_id) for d in results}
    assert targets == {
        (uid("doc:p2024.csv"), uid("doc:p2025.csv")),
        (uid("doc:p2025.csv"), uid("doc:p2026.csv")),
    }
