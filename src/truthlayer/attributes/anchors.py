"""Deterministic measure anchors (design 04, sections 6-8).

Structured measure fields (``measure_unit / currency / tax_basis``, populated
by fact-extract-v5+) are the ONLY objective basis for comparing numeric
facts. This module is deliberately vocabulary-free: it never guesses that
"工作日" means "个工作日" or that two different unit strings are equivalent —
it normalizes surface form and otherwise reports the anchor as missing or
conflicting. The policy decision ("are these two numbers supposed to be the
same thing") lives elsewhere (literal identity / human confirmation).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AnchorVerdict(str, Enum):
    """Three-state result of checking two facts' structural anchors."""

    #: Both sides parse to the same unit/currency dimensions.
    CONSISTENT = "consistent"
    #: Both sides parse, but to mutually exclusive dimensions (e.g.
    #: 元/年·企业 vs 元/人天; 含税 vs 不含税).
    CONFLICT = "conflict"
    #: At least one side carries no parseable anchor for this dimension
    #: (T-02): the model may NOT be trusted blindly, but the pair must not
    #: be silently dropped either — callers route to pending review.
    MISSING = "missing"
    #: Facts are not measure-typed numeric facts at all.
    NOT_MEASURE = "not_measure"


def normalize_dimension(text: str | None) -> str | None:
    """NFKC + remove every whitespace character; casefold.

    Punctuation is intentionally preserved (``元/人天`` vs ``元人天`` must not
    collapse; ``·`` separates compound per-entity units). This is a surface
    normalizer, not a semantic one.
    """
    if not text:
        return None
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(ch for ch in normalized if not ch.isspace())
    normalized = normalized.strip()
    return normalized.casefold() or None


def normalize_name(text: str) -> str:
    """NFKC + strip whitespace AND punctuation + casefold.

    Used for the deterministic canonical-attribute confluence key (§9.0):
    "渠道结算价" and "渠道结算价格。" must merge, unlike unit dimensions where
    punctuation carries meaning.
    """
    normalized = unicodedata.normalize("NFKC", text)
    chars = [
        ch.casefold()
        for ch in normalized
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    ]
    return "".join(chars)


def normalize_number(value: Any) -> float | int | None:
    """Numeric value as int/float.

    v5 facts store JSON numbers. Older rows may hold display strings such as
    ``"22,800"`` (XLSX text cells); thousands separators are stripped as a
    fallback, but non-numeric text yields None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("，", "")
        if not cleaned:
            return None
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return None


def tax_basis_compare(a: str | None, b: str | None) -> AnchorVerdict:
    """Compare tax treatment of two monetary facts.

    ``unknown`` / missing means "the source did not state it" — treated as
    MISSING (cannot confirm comparability), never as a match.
    """
    if a is None and b is None:
        # Non-monetary facts (no tax dimension at all) do not conflict on tax.
        return AnchorVerdict.NOT_MEASURE
    if a is None or b is None:
        return AnchorVerdict.MISSING
    if a == "unknown" or b == "unknown":
        return AnchorVerdict.MISSING
    return (
        AnchorVerdict.CONSISTENT
        if a == b
        else AnchorVerdict.CONFLICT
    )


@dataclass(frozen=True)
class MeasureComparison:
    verdict: AnchorVerdict
    #: Numeric values when both sides parse.
    a_number: float | int | None = None
    b_number: float | int | None = None
    unit_a: str | None = None
    unit_b: str | None = None
    #: True when numeric values are strictly equal (5 == 5.0, commas stripped).
    same_value: bool = False


def measure_compare(
    *,
    a_value: Any,
    a_type: str | None,
    a_unit: str | None,
    a_currency: str | None,
    a_tax: str | None,
    b_value: Any,
    b_type: str | None,
    b_unit: str | None,
    b_currency: str | None,
    b_tax: str | None,
) -> MeasureComparison:
    """Compare two scalar facts through structured measure anchors.

    Decision order:

    1. Both facts must be numeric with at least one unit dimension, else
       ``NOT_MEASURE`` (caller falls back to text / enumeration handling);
    2. unit dimensions present on both but different, or currency present on
       both but different → ``CONFLICT``;
    3. a unit/currency dimension unparseable on either side → ``MISSING``;
    4. tax basis: stated-vs-different → ``CONFLICT``; any unknown → downgrade
       to ``MISSING``;
    5. otherwise numbers are compared strictly for ``same_value``.
    """
    num_a = normalize_number(a_value)
    num_b = normalize_number(b_value)
    unit_a = normalize_dimension(a_unit)
    unit_b = normalize_dimension(b_unit)

    is_measure = (
        a_type == "number"
        and b_type == "number"
        and num_a is not None
        and num_b is not None
        and (unit_a is not None or unit_b is not None)
    )
    if not is_measure:
        return MeasureComparison(
            AnchorVerdict.NOT_MEASURE,
            num_a,
            num_b,
            unit_a,
            unit_b,
        )

    if unit_a is None or unit_b is None:
        return MeasureComparison(
            AnchorVerdict.MISSING, num_a, num_b, unit_a, unit_b
        )
    if unit_a != unit_b:
        return MeasureComparison(
            AnchorVerdict.CONFLICT, num_a, num_b, unit_a, unit_b
        )

    cur_a = (a_currency or "").strip().upper() or None
    cur_b = (b_currency or "").strip().upper() or None
    if cur_a is not None and cur_b is not None and cur_a != cur_b:
        return MeasureComparison(
            AnchorVerdict.CONFLICT, num_a, num_b, unit_a, unit_b
        )

    tax = tax_basis_compare(a_tax, b_tax)
    if tax is AnchorVerdict.CONFLICT:
        return MeasureComparison(
            AnchorVerdict.CONFLICT, num_a, num_b, unit_a, unit_b
        )

    same_value = num_a == num_b
    verdict = AnchorVerdict.MISSING if tax is AnchorVerdict.MISSING else AnchorVerdict.CONSISTENT
    return MeasureComparison(
        verdict, num_a, num_b, unit_a, unit_b, same_value
    )
