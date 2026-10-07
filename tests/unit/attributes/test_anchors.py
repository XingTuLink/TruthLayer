"""Unit tests for deterministic measure anchors (design 04, §6-8).

The whole point of the layer is that it is vocabulary-free: surface forms
either normalize together or they do not, and the model cannot override a
conflicting or missing anchor.
"""

from __future__ import annotations

from truthlayer.attributes import (
    AnchorVerdict,
    measure_compare,
    normalize_dimension,
    normalize_name,
    normalize_number,
    tax_basis_compare,
)


def _compare(**overrides):
    base = dict(
        a_value=22800,
        a_type="number",
        a_unit="元/年·企业",
        a_currency="CNY",
        a_tax="inclusive",
        b_value=23800,
        b_type="number",
        b_unit="元/年·企业",
        b_currency="CNY",
        b_tax="inclusive",
    )
    base.update(overrides)
    return measure_compare(**base)


def test_normalize_dimension_strips_whitespace_keeps_punctuation():
    assert normalize_dimension(" 元 / 年·企业 ") == "元/年·企业"
    # Punctuation carries dimensional meaning — never collapsed.
    assert normalize_dimension("元/人天") != normalize_dimension("元人天")
    assert normalize_dimension(None) is None
    assert normalize_dimension("   ") is None


def test_normalize_name_strips_punctuation():
    assert normalize_name("渠道 结算价。") == "渠道结算价"


def test_normalize_number_handles_strings_and_commas():
    assert normalize_number(5) == 5
    assert normalize_number(5.0) == 5
    assert normalize_number("22,800") == 22800
    assert normalize_number("22，800") == 22800
    assert normalize_number("abc") is None
    assert normalize_number(True) is None


def test_tax_basis_compare_states():
    assert tax_basis_compare(None, None) is AnchorVerdict.NOT_MEASURE
    assert tax_basis_compare("inclusive", None) is AnchorVerdict.MISSING
    assert tax_basis_compare("inclusive", "unknown") is AnchorVerdict.MISSING
    assert tax_basis_compare("inclusive", "inclusive") is AnchorVerdict.CONSISTENT
    assert tax_basis_compare("inclusive", "exclusive") is AnchorVerdict.CONFLICT


def test_consistent_dimensions_different_number_is_conflict_verdict():
    result = _compare()
    assert result.verdict is AnchorVerdict.CONSISTENT
    assert result.same_value is False


def test_consistent_dimensions_equal_number_is_same_value():
    result = _compare(b_value=22800)
    assert result.verdict is AnchorVerdict.CONSISTENT
    assert result.same_value is True


def test_different_unit_is_anchor_conflict():
    result = _compare(b_unit="元/人天")
    assert result.verdict is AnchorVerdict.CONFLICT


def test_different_currency_is_anchor_conflict():
    result = _compare(b_currency="USD")
    assert result.verdict is AnchorVerdict.CONFLICT


def test_tax_conflict_is_anchor_conflict():
    result = _compare(b_tax="exclusive")
    assert result.verdict is AnchorVerdict.CONFLICT


def test_unknown_tax_downgrades_to_missing():
    result = _compare(b_tax="unknown")
    assert result.verdict is AnchorVerdict.MISSING


def test_missing_unit_on_one_side_is_missing_not_conflict():
    result = _compare(b_unit=None)
    assert result.verdict is AnchorVerdict.MISSING


def test_non_number_facts_are_not_measure():
    result = _compare(
        a_value="座区制", a_type="string", a_unit=None, a_currency=None,
        a_tax=None,
        b_value="分区制", b_type="string", b_unit=None, b_currency=None,
        b_tax=None,
    )
    assert result.verdict is AnchorVerdict.NOT_MEASURE


def test_non_money_units_have_no_tax_dimension():
    result = _compare(
        a_value=5, a_unit="人天", a_currency=None, a_tax=None,
        b_value=6, b_unit="人天", b_currency=None, b_tax=None,
    )
    assert result.verdict is AnchorVerdict.CONSISTENT
    assert result.same_value is False
