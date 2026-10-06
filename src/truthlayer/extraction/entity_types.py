"""Controlled equivalence classes for LLM-declared entity types (R8).

The extractor prompt asks for short type labels, but models drift between
near-synonyms across sources — the same sellable item is frequently tagged
``product`` in one document and ``service`` in another. Because entity
identity is keyed by ``(name, type)``, that drift splits one real-world
entity into two rows, which both hides cross-source conflicts and raises
spurious duplicates.

This module is deliberately minimal: it defines **equivalence classes**, not
a full taxonomy. Two declared types are treated as the same class only when
both normalize into an explicit, narrow alias set. Person/org/policy/etc.
intentionally have no class, so a same-name entity across semantic
categories is never auto-merged.
"""

from __future__ import annotations

#: A purchasable offering (a product or a service). This is the only class
#: where cross-source type drift has been observed to split real entities;
#: extend only with concrete, evidenced synonyms.
OFFERING = "offering"

_OFFERING_ALIASES = frozenset(
    {
        "product",
        "service",
        "sku",
        "item",
        "plan",
        "offering",
        "package",
        "subscription",
        # Chinese labels seen in real extractions.
        "产品",
        "服务",
        "套餐",
        "商品",
    }
)


def normalize_entity_type(value: str) -> str:
    """Trim + casefold, mirroring ``entities.normalize_type``."""
    return value.strip().casefold()


def entity_type_class(value: str) -> str | None:
    """Return the controlled class for a declared type, else None.

    A None result means the type has no merge class: it must never be treated
    as equivalent to any other type (including another None).
    """
    if normalize_entity_type(value) in _OFFERING_ALIASES:
        return OFFERING
    return None


def types_equivalent(a: str, b: str) -> bool:
    """True only when both types normalize into the same non-null class."""
    class_a = entity_type_class(a)
    class_b = entity_type_class(b)
    return class_a is not None and class_a == class_b
