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


# -- declaration gate: the quoting document declares its own expiry ---------


def _declared_expiry_docs():
    return [
        make_document(
            "handbook_2021.docx", group="handbook", source_type="corporate"
        ),
        make_document(
            "handbook_2025.docx",
            group="handbook",
            supersedes="handbook_2021.docx",
            source_type="corporate",
        ),
        make_document("travel_2022.pdf", source_type="corporate"),
    ]


def _declared_expiry_facts(
    *,
    stmt_predicate="有效期至",
    stmt_value="2023-12-31",
    stmt_valid_to=date(2023, 12, 31),
    stmt_anchor="quoted",
):
    return [
        make_fact(
            "old",
            subject=PRODUCT,
            predicate="lodging_cap",
            value=350,
            measure_unit=UNIT_DAY,
            document="handbook_2021.docx",
        ),
        make_fact(
            "head",
            subject=PRODUCT,
            predicate="lodging_cap",
            value=500,
            measure_unit=UNIT_DAY,
            document="handbook_2025.docx",
        ),
        make_fact(
            "stmt",
            subject="差旅标准2022版",
            predicate=stmt_predicate,
            value=stmt_value,
            object_type="string",
            document="travel_2022.pdf",
            valid_to=stmt_valid_to,
            valid_to_anchor=stmt_anchor,
        ),
        make_fact(
            "biz",
            subject=PRODUCT,
            predicate="travel_lodging_cap",
            value=350,
            measure_unit=UNIT_DAY,
            document="travel_2022.pdf",
            valid_from=date(2022, 3, 1),
        ),
    ]


def test_silent_when_document_declares_own_quoted_expiry(context):
    """run17: a standalone expired standard matching a handbook chain value
    is a legal-at-issue-time value, not a copied dead value."""
    state = build_state(
        _declared_expiry_facts(), documents=_declared_expiry_docs()
    )

    assert _reuse_candidates(state, context) == []


def test_silent_when_expiry_statement_has_legacy_null_anchor(context):
    """Pre-v6 rows carry no anchor; the expiry statement stays trusted."""
    state = build_state(
        _declared_expiry_facts(stmt_anchor=None),
        documents=_declared_expiry_docs(),
    )

    assert _reuse_candidates(state, context) == []


def test_flags_when_expiry_statement_anchor_not_evidence(context):
    """An inferred (document_scope/calendar_derived) expiry is not a
    declaration the detector may structurally trust."""
    state = build_state(
        _declared_expiry_facts(stmt_anchor="calendar_derived"),
        documents=_declared_expiry_docs(),
    )

    hits = _reuse_candidates(state, context)
    assert len(hits) == 1
    assert hits[0].target_id == uid("fact:biz")


def test_flags_when_declared_expiry_still_in_future(context):
    state = build_state(
        _declared_expiry_facts(
            stmt_value="2027-12-31", stmt_valid_to=date(2027, 12, 31)
        ),
        documents=_declared_expiry_docs(),
    )

    hits = _reuse_candidates(state, context)
    assert len(hits) == 1
    assert hits[0].target_id == uid("fact:biz")


def test_flags_when_statement_is_not_a_validity_end_claim(context):
    # Start-date statements and arbitrary metadata must not silence R16.
    state = build_state(
        _declared_expiry_facts(
            stmt_predicate="生效日期",
            stmt_value="2022-03-01",
            stmt_valid_to=date(2022, 3, 1),
        ),
        documents=_declared_expiry_docs(),
    )

    hits = _reuse_candidates(state, context)
    assert len(hits) == 1
    assert hits[0].target_id == uid("fact:biz")


def test_flags_when_statement_lives_on_another_document(context):
    """A quoted expiry statement on the chain's dead edition does not mark
    the quoting document as expired — the statement travels with its doc."""
    facts = _declared_expiry_facts()
    facts[2] = make_fact(
        "stmt",
        subject="差旅标准2022版",
        predicate="有效期至",
        value="2023-12-31",
        object_type="string",
        document="handbook_2021.docx",
        valid_to=date(2023, 12, 31),
        valid_to_anchor="quoted",
    )
    state = build_state(facts, documents=_declared_expiry_docs())

    hits = _reuse_candidates(state, context)
    assert len(hits) == 1
    assert hits[0].old_document_id == uid("doc:handbook_2021.docx")
    assert hits[0].new_document_id == uid("doc:handbook_2025.docx")
