"""Unit tests for valid_to provenance classification (fact-extract-v6).

Three classes, date always preserved:
quoted (in the fact's own quote) / document_scope (elsewhere in the chunk,
e.g. a document header) / calendar_derived (appears nowhere — inferred from
a period label).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from truthlayer.extraction.schemas import RawFact
from truthlayer.extraction.validity_anchor import (
    ANCHOR_CALENDAR_DERIVED,
    ANCHOR_DOCUMENT_SCOPE,
    ANCHOR_QUOTED,
    annotate_validity_anchors,
    classify_valid_to,
    date_is_quoted,
)


def _fact(quote: str, valid_to: str | None = "2026-09-30") -> RawFact:
    return RawFact(
        subject="抽纸",
        predicate="领用单价",
        object_value=6,
        object_type="number",
        unit="包",
        valid_to=valid_to,
        valid_from="2026-07-01",
        observed_at="2026-07-01",
        quote=quote,
    )


# ---- date_is_quoted -------------------------------------------------------

def test_all_common_date_notations_are_recognized() -> None:
    for literal in (
        "有效期至2026-09-30止",
        "有效期至2026/09/30止",
        "有效期至2026/9/30止",
        "截止2026.09.30",
        "自2026年09月30日起废止",
        "自2026年9月30日起废止",
    ):
        assert date_is_quoted("2026-09-30", literal), literal


def test_whitespace_including_ideographic_space_is_ignored() -> None:
    assert date_is_quoted("2026-09-30", "执行期限：2026 年 9 月 30 日　截止")
    assert date_is_quoted("2026-09-30", "执行期限：2026 年 09 月 30 日截止")


def test_month_day_without_year_is_not_an_anchor() -> None:
    # A bare month/day could belong to any year; require the full date.
    assert not date_is_quoted("2026-09-30", "每年9月30日统一结算")


def test_unrelated_date_is_not_an_anchor() -> None:
    assert not date_is_quoted("2026-09-30", "本文件发布于2026年7月1日")


# ---- classify_valid_to ----------------------------------------------------

def test_quoted_when_date_is_inside_fact_quote() -> None:
    quote = "夏季促销：全场85折，优惠有效期至2026年09月30日，之后恢复原价"
    assert (
        classify_valid_to("2026-09-30", quote, chunk_text=quote)
        == ANCHOR_QUOTED
    )


def test_document_scope_when_date_only_in_chunk_header() -> None:
    # The run16 P0 shape: a document-level validity statement sits in the
    # header; the row fact's own quote carries no date.
    row_quote = (
        "物品编码: BT-007; 物品名称: 白板笔; 单位: 支; "
        "领用单价: 3.5; 执行日期：2026年7月1日"
    )
    chunk = (
        "办公用品领用目录（2026年第三季度）\n"
        "本文件有效期至2026年09月30日，逾期另行通知。\n"
        f"{row_quote}"
    )
    assert (
        classify_valid_to("2026-09-30", row_quote, chunk)
        == ANCHOR_DOCUMENT_SCOPE
    )


def test_calendar_derived_when_date_appears_nowhere() -> None:
    # The model inferred quarter end from the title "第三季度"; no literal
    # 2026-09-30 exists anywhere in the source text.
    row_quote = (
        "物品编码: BT-007; 物品名称: 白板笔; 单位: 支; "
        "领用单价: 3.5; 执行日期：2026年7月1日"
    )
    chunk = f"办公用品领用目录（2026年第三季度）\n{row_quote}"
    assert (
        classify_valid_to("2026-09-30", row_quote, chunk)
        == ANCHOR_CALENDAR_DERIVED
    )


def test_quoted_takes_precedence_over_document_scope() -> None:
    quote = "本条款有效期至2026-09-30"
    chunk = f"抬头：本文件有效期至2026年09月30日\n{quote}"
    assert (
        classify_valid_to("2026-09-30", quote, chunk) == ANCHOR_QUOTED
    )


def test_missing_chunk_text_falls_back_to_calendar_derived() -> None:
    assert (
        classify_valid_to("2026-09-30", "无日期的行事实", None)
        == ANCHOR_CALENDAR_DERIVED
    )


# ---- annotate_validity_anchors --------------------------------------------

def test_annotate_tags_each_class_and_preserves_dates() -> None:
    quoted = _fact("有效期至2026-09-30", valid_to="2026-09-30")
    scoped = _fact("行事实：单价3.5元，执行日期：2026年7月1日")
    inferred = _fact("行事实：单价6元", valid_to="2026-12-31")
    undated = _fact("长期有效", valid_to=None)
    chunk = (
        "抬头：本文件有效期至2026年09月30日\n"
        "有效期至2026-09-30\n"
        "行事实：单价3.5元，执行日期：2026年7月1日\n"
        "行事实：单价6元\n长期有效"
    )
    counts = annotate_validity_anchors(
        [quoted, scoped, inferred, undated], chunk
    )
    assert counts[ANCHOR_QUOTED] == 1
    assert counts[ANCHOR_DOCUMENT_SCOPE] == 1
    assert counts[ANCHOR_CALENDAR_DERIVED] == 1
    assert quoted.valid_to_anchor == ANCHOR_QUOTED
    assert scoped.valid_to_anchor == ANCHOR_DOCUMENT_SCOPE
    assert inferred.valid_to_anchor == ANCHOR_CALENDAR_DERIVED
    # Dates are never deleted; undated facts stay untagged.
    assert quoted.valid_to == "2026-09-30"
    assert scoped.valid_to == "2026-09-30"
    assert inferred.valid_to == "2026-12-31"
    assert undated.valid_to_anchor is None


def test_anchor_is_not_part_of_llm_payload() -> None:
    fact = _fact("有效期至2026-09-30")
    annotate_validity_anchors([fact], "有效期至2026-09-30")
    # The tag must not leak into model_dump (LLM JSON in/out surface).
    assert "valid_to_anchor" not in fact.model_dump()
    # And the model cannot self-certify an anchor on input (extra="forbid").
    with pytest.raises(ValidationError):
        RawFact(
            subject="x",
            predicate="p",
            object_value=1,
            object_type="number",
            quote="q",
            valid_to_anchor="quoted",  # type: ignore[call-arg]
        )
