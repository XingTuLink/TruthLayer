"""Unit tests for R16 — verbatim reuse of a superseded value (design 04 §11).

A *current* document that copies a dead edition's value word-for-word while
the version-chain head already carries another value is a deterministic,
model-free stale path. Every constraint is exercised here, including the
negative cases that keep bare value collisions silent.
"""

from __future__ import annotations

from datetime import date

import pytest

from truthlayer.detection.scoring import STRUCTURAL_CONFIDENCE
from truthlayer.detection.stale import StaleDetector
from truthlayer.domain.enums import DriftType, Severity

from .conftest import build_state, make_document, make_fact, uid

UNIT_YEAR = "元/年·企业"
UNIT_DAY = "元/人天"
PRODUCT = "云客服专业版"
SERVICE = "培训服务"


def _version_chain_docs() -> list:
    return [
        make_document(
            "price_2025.csv", group="price", source_type="corporate"
        ),
        make_document(
            "price_2026.csv",
            group="price",
            supersedes="price_2025.csv",
            source_type="corporate",
        ),
        make_document("channel_2026.pdf", source_type="corporate"),
    ]


def _reuse_facts(*, current_value=22800, current_unit=UNIT_YEAR,
                 current_subject=PRODUCT, current_predicate="channel_price",
                 current_valid_to=date(2026, 12, 31), head_value=23800,
                 old_value=22800, old_unit=UNIT_YEAR, old_subject=PRODUCT):
    return [
        make_fact(
            "old",
            subject=old_subject,
            predicate="list_price",
            value=old_value,
            measure_unit=old_unit,
            document="price_2025.csv",
            valid_to=date(2025, 12, 31),
        ),
        make_fact(
            "head",
            subject=PRODUCT,
            predicate="list_price",
            value=head_value,
            measure_unit=UNIT_YEAR,
            document="price_2026.csv",
            valid_from=date(2026, 1, 1),
        ),
        make_fact(
            "current",
            subject=current_subject,
            predicate=current_predicate,
            value=current_value,
            measure_unit=current_unit,
            document="channel_2026.pdf",
            valid_from=date(2026, 7, 1),
            valid_to=current_valid_to,
        ),
    ]


def _reuse_candidates(state, context):
    return [
        c
        for c in StaleDetector().detect(state, context)
        if c.drift_type == DriftType.REUSED_STALE_VALUE
    ]


def test_flags_current_document_quoting_dead_edition_value(context):
    state = build_state(_reuse_facts(), documents=_version_chain_docs())

    hits = _reuse_candidates(state, context)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.target_id == uid("fact:current")
    assert hit.old_document_id == uid("doc:price_2025.csv")
    assert hit.new_document_id == uid("doc:price_2026.csv")
    assert hit.new_fact_id == uid("fact:head")
    assert hit.severity == Severity.HIGH
    assert hit.confidence == STRUCTURAL_CONFIDENCE
    assert hit.detail["reason"] == "reused_superseded_value"
    assert hit.detail["reused_value"] == 22800
    assert hit.detail["head_value"] == 23800
    assert hit.detail["old_source"] == "price_2025.csv"
    assert hit.detail["new_source"] == "price_2026.csv"
    assert hit.detail["source"] == "channel_2026.pdf"


def test_walks_two_editions_back_and_targets_farthest_match(context):
    docs = [
        make_document("price_2024.csv", group="price", source_type="corporate"),
        make_document(
            "price_2025.csv",
            group="price",
            supersedes="price_2024.csv",
            source_type="corporate",
        ),
        make_document(
            "price_2026.csv",
            group="price",
            supersedes="price_2025.csv",
            source_type="corporate",
        ),
        make_document("channel_2026.pdf", source_type="corporate"),
    ]
    facts = [
        make_fact(
            "v1", subject=SERVICE, value=1200, measure_unit=UNIT_DAY,
            document="price_2024.csv", valid_to=date(2024, 12, 31),
        ),
        make_fact(
            "v2", subject=SERVICE, value=1500, measure_unit=UNIT_DAY,
            document="price_2025.csv", valid_to=date(2025, 12, 31),
        ),
        make_fact(
            "v3", subject=SERVICE, value=1500, measure_unit=UNIT_DAY,
            document="price_2026.csv", valid_from=date(2026, 1, 1),
        ),
        make_fact(
            "current", subject=SERVICE, predicate="channel_price", value=1200,
            measure_unit=UNIT_DAY, document="channel_2026.pdf",
            valid_from=date(2026, 7, 1), valid_to=date(2026, 12, 31),
        ),
    ]
    state = build_state(facts, documents=docs)

    hits = _reuse_candidates(state, context)

    assert len(hits) == 1
    assert hits[0].old_document_id == uid("doc:price_2024.csv")
    assert hits[0].new_document_id == uid("doc:price_2026.csv")


def test_prefers_nearest_dead_edition_when_value_repeats(context):
    docs = [
        make_document("price_2024.csv", group="price", source_type="corporate"),
        make_document(
            "price_2025.csv",
            group="price",
            supersedes="price_2024.csv",
            source_type="corporate",
        ),
        make_document(
            "price_2026.csv",
            group="price",
            supersedes="price_2025.csv",
            source_type="corporate",
        ),
        make_document("channel_2026.pdf", source_type="corporate"),
    ]
    facts = [
        make_fact("v1", subject=SERVICE, value=1200, measure_unit=UNIT_DAY,
                  document="price_2024.csv", valid_to=date(2024, 12, 31)),
        make_fact("v2", subject=SERVICE, value=1200, measure_unit=UNIT_DAY,
                  document="price_2025.csv", valid_to=date(2025, 12, 31)),
        make_fact("v3", subject=SERVICE, value=1500, measure_unit=UNIT_DAY,
                  document="price_2026.csv", valid_from=date(2026, 1, 1)),
        make_fact("current", subject=SERVICE, predicate="channel_price",
                  value=1200, measure_unit=UNIT_DAY,
                  document="channel_2026.pdf", valid_from=date(2026, 7, 1),
                  valid_to=date(2026, 12, 31)),
    ]
    state = build_state(facts, documents=docs)

    hits = _reuse_candidates(state, context)

    assert len(hits) == 1
    assert hits[0].old_document_id == uid("doc:price_2025.csv")


def test_thousands_separator_normalizes_to_verbatim_match(context):
    state = build_state(
        _reuse_facts(current_value="22,800"),
        documents=_version_chain_docs(),
    )

    hits = _reuse_candidates(state, context)

    assert len(hits) == 1
    assert hits[0].detail["reused_value"] == "22,800"


@pytest.mark.parametrize(
    "kwargs",
    [
        # Head still carries the same number — legitimate flat pricing.
        {"head_value": 22800},
        # Same number, different subject on the current fact.
        {"current_subject": SERVICE},
        # Same number, different measure dimension.
        {"current_unit": UNIT_DAY},
        # No parsed unit anchor on the current fact.
        {"current_unit": None},
    ],
)
def test_silent_when_constraints_do_not_hold(context, kwargs):
    state = build_state(
        _reuse_facts(**kwargs), documents=_version_chain_docs()
    )

    assert _reuse_candidates(state, context) == []


def test_silent_without_explicit_version_chain(context):
    docs = [
        make_document("price_2025.csv", source_type="corporate"),
        make_document("price_2026.csv", source_type="corporate"),
        make_document("channel_2026.pdf", source_type="corporate"),
    ]
    state = build_state(_reuse_facts(), documents=docs)

    assert _reuse_candidates(state, context) == []


def test_silent_when_current_fact_window_expired(context):
    state = build_state(
        _reuse_facts(current_valid_to=date(2026, 8, 31)),
        documents=_version_chain_docs(),
    )

    assert _reuse_candidates(state, context) == []


def test_silent_for_non_number_facts(context):
    facts = _reuse_facts()
    facts[-1] = make_fact(
        "current",
        subject=PRODUCT,
        predicate="channel_price",
        value="按官网最新报价",
        object_type="string",
        document="channel_2026.pdf",
        valid_from=date(2026, 7, 1),
        valid_to=date(2026, 12, 31),
    )
    state = build_state(facts, documents=_version_chain_docs())

    assert _reuse_candidates(state, context) == []
