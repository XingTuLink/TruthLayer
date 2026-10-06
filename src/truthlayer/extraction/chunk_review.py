"""Deterministic propagation of chunk-level review dates (R6).

Small models often read a sentence like "最近一次复核日期 2025-07-15，确认
继续执行" as a standalone predicate fact while leaving the subject's other
facts without ``observed_at``. The stale detector then has no age signal and
(rightly) stays silent — this is why the high-temperature ground truth (G6)
never surfaced with the 7b model even though the date was extracted.

This is a post-LLM, fully deterministic fix, applied within one chunk and
aligned by subject. It is deliberately strict to avoid polluting facts:

* only scalar ``date`` facts count as a review timestamp, and only when the
  predicate expresses "reviewed / confirmed still valid" semantics;
* "effective/published/start" dates are explicitly excluded (those drive
  valid_from, not observation metadata);
* propagation is per subject; one subject's review date never reaches another
  subject's facts;
* a subject with more than one distinct review date propagates nothing;
* an existing observed_at is never overwritten, and the timestamp fact
  itself is not back-filled.
"""

from __future__ import annotations

from truthlayer.extraction.schemas import RawFact

#: Predicates expressing "last reviewed / confirmed still in force".
_REVIEW_MARKERS = (
    "复核",
    "复审",
    "审核",
    "审阅",
    "确认有效",
    "确认仍",
    "review",  # covers review / reviewed / last reviewed
)

#: Predicates that may contain a date but must NOT be treated as a review.
_EXCLUDE_MARKERS = (
    "生效",
    "发布",
    "执行",
    "实施",
    "开始",
    "起始",
    "有效期",
    "valid",
    "effective",
    "publish",
    "start",
)


def _review_date(fact: RawFact) -> str | None:
    """Return the ISO date if a fact is a 'last reviewed' timestamp, else None."""
    if fact.object_type != "date" or fact.object_value is None:
        return None
    predicate = fact.predicate.casefold()
    if any(marker in predicate for marker in _EXCLUDE_MARKERS):
        return None
    if any(marker in predicate for marker in _REVIEW_MARKERS):
        return str(fact.object_value)
    return None


def propagate_review_dates(facts: list[RawFact]) -> int:
    """Fill missing ``observed_at`` from each subject's unique review date.

    Mutates facts in place and returns how many non-timestamp facts received
    a propagated date.
    """
    review_dates: dict[str, set[str]] = {}
    for fact in facts:
        date_value = _review_date(fact)
        if date_value is not None:
            review_dates.setdefault(
                fact.subject.strip().casefold(), set()
            ).add(date_value)

    # Only a subject with exactly one distinct review date is unambiguous.
    unique_date: dict[str, str] = {
        subject: next(iter(dates))
        for subject, dates in review_dates.items()
        if len(dates) == 1
    }
    if not unique_date:
        return 0

    propagated = 0
    for fact in facts:
        if fact.observed_at is not None:
            continue
        if _review_date(fact) is not None:
            continue  # never back-fill the timestamp fact itself
        date_value = unique_date.get(fact.subject.strip().casefold())
        if date_value is not None:
            fact.observed_at = date_value
            propagated += 1
    return propagated
