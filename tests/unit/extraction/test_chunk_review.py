"""Unit tests for deterministic review-date propagation (R6)."""

from __future__ import annotations

from truthlayer.extraction.chunk_review import propagate_review_dates
from truthlayer.extraction.schemas import RawFact


def _fact(
    subject: str,
    predicate: str,
    *,
    value: str,
    value_type: str = "date",
    observed_at: str | None = None,
) -> RawFact:
    return RawFact(
        subject=subject,
        predicate=predicate,
        object_value=value,
        object_type=value_type,  # type: ignore[arg-type]
        observed_at=observed_at,
        quote="原文片段",
    )


def test_review_date_propagates_to_same_subject_facts() -> None:
    ts = _fact("高温津贴", "最近一次复核日期", value="2025-07-15")
    months = _fact("高温津贴", "发放月份", value="6-8月", value_type="string")
    amount = _fact("高温津贴", "月标准", value="300", value_type="number")

    assert propagate_review_dates([ts, months, amount]) == 2
    assert months.observed_at == "2025-07-15"
    assert amount.observed_at == "2025-07-15"
    # The timestamp fact itself is not back-filled.
    assert ts.observed_at is None


def test_existing_observed_at_is_never_overwritten() -> None:
    ts = _fact("政策A", "复核日期", value="2025-07-15")
    kept = _fact(
        "政策A", "月标准", value="300", value_type="number",
        observed_at="2024-01-01",
    )
    assert propagate_review_dates([ts, kept]) == 0
    assert kept.observed_at == "2024-01-01"


def test_review_date_does_not_cross_subjects() -> None:
    ts = _fact("政策A", "最近复核日期", value="2025-07-15")
    other = _fact("政策B", "月标准", value="300", value_type="number")
    assert propagate_review_dates([ts, other]) == 0
    assert other.observed_at is None


def test_multiple_distinct_review_dates_are_not_propagated() -> None:
    ts1 = _fact("政策A", "复核日期", value="2025-07-15")
    ts2 = _fact("政策A", "复核日期", value="2026-01-10")
    target = _fact("政策A", "月标准", value="300", value_type="number")
    assert propagate_review_dates([ts1, ts2, target]) == 0
    assert target.observed_at is None


def test_repeated_identical_review_date_is_one_unique_value() -> None:
    ts1 = _fact("政策A", "复核日期", value="2025-07-15")
    ts2 = _fact("政策A", "最近一次复核日期", value="2025-07-15")
    target = _fact("政策A", "月标准", value="300", value_type="number")
    assert propagate_review_dates([ts1, ts2, target]) == 1
    assert target.observed_at == "2025-07-15"


def test_effective_and_publish_dates_are_not_review_dates() -> None:
    for predicate in ("生效日期", "发布日期", "执行起始日", "effective date"):
        ts = _fact("政策A", predicate, value="2026-01-01")
        target = _fact("政策A", "月标准", value="300", value_type="number")
        assert propagate_review_dates([ts, target]) == 0, predicate
        assert target.observed_at is None


def test_non_date_review_predicate_does_not_propagate() -> None:
    # A textual value (even with "复核" in the predicate) is not a date.
    ts = _fact(
        "政策A", "复核负责人", value="张三组", value_type="string"
    )
    target = _fact("政策A", "月标准", value="300", value_type="number")
    assert propagate_review_dates([ts, target]) == 0
    assert target.observed_at is None


def test_empty_fact_list() -> None:
    assert propagate_review_dates([]) == 0
