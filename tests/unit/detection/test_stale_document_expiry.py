"""Document-level self-declared expiry (backlog: 文档级失效声明).

A standalone document whose own evidence-anchored validity-end statement is
already past produces exactly one ``confirmed_stale`` / target=document card;
no date is propagated onto its business facts. Documents on an explicit
version chain keep using the superseded document-level signal instead.
"""

from __future__ import annotations

from datetime import date

import pytest

from truthlayer.detection.scoring import STRUCTURAL_CONFIDENCE
from truthlayer.detection.stale import StaleDetector
from truthlayer.domain.enums import DriftType, Severity, TargetType

from .conftest import build_state, make_document, make_fact, uid

DOC_NAME = "travel_2022.pdf"
NEW_NAME = "travel_2025.pdf"
STMT_SUBJECT = "差旅标准2022版"
UNIT_NIGHT = "元/晚"


def _docs(*, chain: bool = False) -> list:
    if not chain:
        return [make_document(DOC_NAME, source_type="corporate")]
    return [
        make_document(
            DOC_NAME, group="travel", source_type="corporate"
        ),
        make_document(
            NEW_NAME,
            group="travel",
            supersedes=DOC_NAME,
            source_type="corporate",
        ),
    ]


def _stmt(
    *,
    fid: str = "stmt",
    document: str = DOC_NAME,
    predicate: str = "有效期至",
    value: str = "2023-12-31",
    valid_to=date(2023, 12, 31),
    anchor: str | None = "quoted",
) :
    return make_fact(
        fid,
        subject=STMT_SUBJECT,
        predicate=predicate,
        value=value,
        object_type="string",
        document=document,
        valid_to=valid_to,
        valid_to_anchor=anchor,
    )


def _biz(
    fid: str = "biz",
    *,
    document: str = DOC_NAME,
    predicate: str = "lodging_cap",
    value=350,
    valid_from=date(2022, 3, 1),
):
    return make_fact(
        fid,
        subject="住宿上限",
        predicate=predicate,
        value=value,
        measure_unit=UNIT_NIGHT,
        document=document,
        valid_from=valid_from,
    )


def _doc_candidates(state, context):
    return [
        c
        for c in StaleDetector().detect(state, context)
        if c.target_type == TargetType.DOCUMENT
    ]


def test_emits_one_document_card_for_standalone_expired_statement(context):
    state = build_state(
        [_stmt(), _biz()],
        documents=_docs(),
    )

    hits = _doc_candidates(state, context)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.drift_type == DriftType.CONFIRMED_STALE
    assert hit.target_type == TargetType.DOCUMENT
    assert hit.target_id == uid(f"doc:{DOC_NAME}")
    assert hit.old_document_id == uid(f"doc:{DOC_NAME}")
    assert hit.new_document_id is None
    assert hit.old_fact_id == uid("fact:stmt")
    assert hit.subject_entity_id == uid(f"entity:{STMT_SUBJECT}")
    assert hit.predicate == "有效期至"
    assert hit.severity == Severity.MEDIUM
    assert hit.confidence == STRUCTURAL_CONFIDENCE
    assert hit.detail["reason"] == "document_self_declared_expired"
    assert hit.detail["valid_to"] == "2023-12-31"
    assert hit.detail["valid_to_anchor"] == "quoted"
    assert hit.detail["old_source"] == DOC_NAME
    assert hit.fingerprint_key == (
        DriftType.CONFIRMED_STALE.value,
        "document",
        str(uid(f"doc:{DOC_NAME}")),
    )


def test_legacy_null_anchor_statement_still_trusted(context):
    state = build_state(
        [_stmt(anchor=None), _biz()],
        documents=_docs(),
    )

    assert len(_doc_candidates(state, context)) == 1


@pytest.mark.parametrize("predicate", ["有效期至", "有效期截止", "失效日期"])
def test_validity_end_predicate_variants_emit_card(context, predicate):
    state = build_state(
        [_stmt(predicate=predicate), _biz()],
        documents=_docs(),
    )

    hits = _doc_candidates(state, context)
    assert len(hits) == 1
    assert hits[0].predicate == predicate


def test_no_card_when_document_has_successor(context):
    state = build_state(
        [
            _stmt(),
            _biz(),
            _biz(
                "biz_new",
                document=NEW_NAME,
                value=500,
                valid_from=date(2025, 1, 1),
            ),
        ],
        documents=_docs(chain=True),
    )

    assert _doc_candidates(state, context) == []


def test_no_card_when_statement_still_in_future(context):
    state = build_state(
        [
            _stmt(value="2027-12-31", valid_to=date(2027, 12, 31)),
            _biz(valid_from=date(2026, 9, 1)),
        ],
        documents=_docs(),
    )

    assert _doc_candidates(state, context) == []


@pytest.mark.parametrize("anchor", ["document_scope", "calendar_derived"])
def test_no_card_when_statement_anchor_not_evidence(context, anchor):
    state = build_state(
        [_stmt(anchor=anchor), _biz()],
        documents=_docs(),
    )

    assert _doc_candidates(state, context) == []


def test_no_card_for_start_date_statement(context):
    # A past 生效日期 is a start-date claim, never an expiry declaration.
    state = build_state(
        [
            _stmt(
                predicate="生效日期",
                value="2022-03-01",
                valid_to=date(2022, 3, 1),
            ),
            _biz(),
        ],
        documents=_docs(),
    )

    assert _doc_candidates(state, context) == []


def test_statement_fact_never_emits_its_own_fact_level_card(context):
    # "失效日期" is a validity-end predicate but NOT in the immutable
    # metadata vocabulary, so the statement-shape guard itself must keep the
    # statement fact silent at fact granularity.
    state = build_state(
        [_stmt(predicate="失效日期"), _biz()],
        documents=_docs(),
    )

    all_candidates = StaleDetector().detect(state, context)
    fact_rows = [c for c in all_candidates if c.target_type == TargetType.FACT]
    assert all(c.old_fact_id != uid("fact:stmt") for c in fact_rows)
    # The business fact keeps its age-only review nudge, not a confirmed row.
    assert not any(
        c.target_type == TargetType.FACT
        and c.drift_type == DriftType.CONFIRMED_STALE
        for c in fact_rows
    )


def test_business_facts_do_not_inherit_statement_date(context):
    state = build_state(
        [_stmt(), _biz()],
        documents=_docs(),
    )

    all_candidates = StaleDetector().detect(state, context)

    biz_rows = [c for c in all_candidates if c.old_fact_id == uid("fact:biz")]
    assert len(biz_rows) == 1
    biz = biz_rows[0]
    assert biz.drift_type == DriftType.POSSIBLY_STALE
    assert biz.severity == Severity.WARNING
    assert biz.detail["reason"] == "age_over_threshold"


def test_one_card_per_doc_with_multiple_statements(context):
    state = build_state(
        [
            _stmt(fid="stmt_a"),
            _stmt(fid="stmt_b", predicate="失效日期"),
            _biz(),
        ],
        documents=_docs(),
    )

    hits = _doc_candidates(state, context)
    assert len(hits) == 1
    assert hits[0].old_fact_id in {
        uid("fact:stmt_a"),
        uid("fact:stmt_b"),
    }


def test_statement_on_another_document_does_not_mark_this_doc(context):
    state = build_state(
        [
            _stmt(fid="stmt", document=DOC_NAME),
            _biz(fid="biz_other", document=NEW_NAME),
        ],
        documents=[
            make_document(DOC_NAME, source_type="corporate"),
            make_document(NEW_NAME, source_type="corporate"),
        ],
    )

    hits = _doc_candidates(state, context)
    assert {h.target_id for h in hits} == {uid(f"doc:{DOC_NAME}")}
