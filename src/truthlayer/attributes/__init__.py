"""Attribute semantic resolution (design doc 04, phase 1).

Predicate text is treated as an alias of a resolved *canonical attribute*
instead of being compared literally. Phase 1 runs the LLM partition + value
gating in-process (no registry tables), is gray-released on pricing sources,
and can only ever produce:

* blocking drift candidates via the pre-existing literal-identity path;
* three non-blocking audit channels: pending review, cross-attribute review,
  normalized-equivalent suppressions.

The LLM never gains conviction power — see design sections 5/6/8.
"""

from truthlayer.attributes.anchors import (
    AnchorVerdict,
    MeasureComparison,
    measure_compare,
    normalize_dimension,
    normalize_name,
    normalize_number,
    tax_basis_compare,
)
from truthlayer.attributes.model import (
    AttributeResolution,
    ChannelItem,
    ChannelKind,
    CanonicalAttribute,
)
from truthlayer.attributes.resolver import AttributeResolver

__all__ = [
    "AnchorVerdict",
    "AttributeResolver",
    "AttributeResolution",
    "CanonicalAttribute",
    "ChannelItem",
    "ChannelKind",
    "MeasureComparison",
    "measure_compare",
    "normalize_dimension",
    "normalize_name",
    "normalize_number",
    "tax_basis_compare",
]
