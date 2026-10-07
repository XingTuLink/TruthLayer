"""Deterministic provenance classification of validity end dates.

The stale detector treats ``valid_to`` as a hard invalidation signal, so a
date the model invents must not carry the same weight as a date the source
document states. Rather than silently dropping inferred dates (which would
erase genuine, answer-key-valid cases such as a documented Q2 promotional
window ending at quarter close), every extracted ``valid_to`` is tagged
with how it is anchored to evidence:

* ``quoted``           — the date occurs verbatim inside the fact's own
                         ``quote``. Deterministic expiry, high confidence.
* ``document_scope``   — the date occurs verbatim elsewhere in the same
                         chunk (typically a document-level header such as
                         "本文件有效期至 2023-12-31") but not in this fact's
                         own quote. A real date with document-level, not
                         row-level, provenance.
* ``calendar_derived`` — the date appears nowhere in the chunk text; it was
                         derived from a period/edition label ("Q3",
                         "第三季度") or common sense.

The detector maps the first class to ``confirmed_stale`` and the other two
to the non-blocking ``possibly_stale`` review channel. Classification uses
no vocabulary and makes no semantic judgment — only literal date matching
against the quote and the chunk text; a document-level validity *statement*
mechanism (not propagation) is tracked separately.
"""

from __future__ import annotations

import re
from collections import Counter

from truthlayer.extraction.schemas import RawFact

ANCHOR_QUOTED = "quoted"
ANCHOR_DOCUMENT_SCOPE = "document_scope"
ANCHOR_CALENDAR_DERIVED = "calendar_derived"

#: Includes the ideographic space U+3000 and the no-break space U+00A0,
#: both common in Chinese corporate documents.
_WHITESPACE = re.compile(r"[\s　 ]+")


def _strip_whitespace(text: str) -> str:
    return _WHITESPACE.sub("", text)


def _date_literals(iso_date: str) -> tuple[str, ...]:
    """Common written forms of one ISO date, with and without zero padding."""
    year, month, day = iso_date.split("-")
    m, d = str(int(month)), str(int(day))
    return (
        f"{year}-{month}-{day}",
        f"{year}-{m}-{d}",
        f"{year}/{month}/{day}",
        f"{year}/{m}/{d}",
        f"{year}.{month}.{day}",
        f"{year}.{m}.{d}",
        f"{year}年{month}月{day}日",
        f"{year}年{m}月{d}日",
    )


def date_is_quoted(iso_date: str, text: str) -> bool:
    """Whether the date appears verbatim in text under any common form."""
    haystack = _strip_whitespace(text)
    return any(
        _strip_whitespace(literal) in haystack
        for literal in _date_literals(iso_date)
    )


def classify_valid_to(
    iso_date: str,
    fact_quote: str,
    chunk_text: str | None,
) -> str:
    """Assign the evidence-anchoring class of one validity end date."""
    if date_is_quoted(iso_date, fact_quote):
        return ANCHOR_QUOTED
    if chunk_text and date_is_quoted(iso_date, chunk_text):
        return ANCHOR_DOCUMENT_SCOPE
    return ANCHOR_CALENDAR_DERIVED


def annotate_validity_anchors(
    facts: list[RawFact],
    chunk_text: str | None,
) -> Counter:
    """Tag every fact carrying ``valid_to`` with its anchoring class.

    The tag is stored on the (non-LLM) private attribute of RawFact and
    returned as a class -> count Counter for observability.
    """
    counts: Counter = Counter()
    for fact in facts:
        if fact.valid_to is None:
            continue
        anchor = classify_valid_to(fact.valid_to, fact.quote, chunk_text)
        fact.valid_to_anchor = anchor
        counts[anchor] += 1
    return counts
