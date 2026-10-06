"""Unit tests for the controlled entity-type equivalence classes (R8)."""

from __future__ import annotations

import pytest

from truthlayer.extraction.entity_types import (
    OFFERING,
    entity_type_class,
    normalize_entity_type,
    types_equivalent,
)


@pytest.mark.parametrize(
    "raw",
    ["product", "Product", " service ", "SKU", "plan", "套餐", "产品", "服务"],
)
def test_offering_aliases_map_to_one_class(raw: str) -> None:
    assert entity_type_class(raw) == OFFERING
    assert normalize_entity_type(raw) in {
        "product",
        "service",
        "sku",
        "plan",
        "套餐",
        "产品",
        "服务",
    }


@pytest.mark.parametrize(
    "a,b",
    [
        ("product", "service"),
        ("Service", "PRODUCT"),
        ("sku", "产品"),
        ("套餐", "subscription"),
        ("offering", "package"),
    ],
)
def test_equivalent_within_offering_class(a: str, b: str) -> None:
    assert types_equivalent(a, b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("person", "product"),
        ("org", "service"),
        ("policy", "product"),
        ("customer", "service"),
        ("unknown", "product"),
        ("person", "org"),
        ("device", "component"),
    ],
)
def test_not_equivalent_across_semantic_categories(a: str, b: str) -> None:
    assert not types_equivalent(a, b)


def test_two_unclassed_types_are_not_equivalent() -> None:
    # Both map to None, but None must not act as a wildcard merge class.
    assert not types_equivalent("device", "region")
    assert entity_type_class("device") is None
    assert entity_type_class("") is None
