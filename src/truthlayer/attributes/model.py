"""In-process attribute resolution model and audit channels (phase 1).

Nothing in this module is persisted yet: the phase-2 registry tables will
replace the in-memory bindings. Channel items are deliberately plain
dataclasses — they are *not* drifts, never carry fingerprint state and never
influence ``ci.fail_on`` / exit codes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from truthlayer.attributes.anchors import normalize_name


class ChannelKind(str, Enum):
    #: Model thinks the two facts may contradict but the attribute identity
    #: lacks human confirmation / objective anchors (identity B, T-02, A×text).
    PENDING_REVIEW = "pending_review"
    #: Structurally suspicious pair (same subject, same parseable unit,
    #: different value, overlapping window, cross-document, literals differ)
    #: that did not land in one canonical attribute — regardless of why
    #: (model split, never co-judged, shard split, global miss).
    CROSS_ATTRIBUTE_REVIEW = "cross_attribute_review"
    #: A model semantic call (alias merge / text equivalence) suppressed a
    #: pair whose surface forms differed. Audit trail in case the model hid a
    #: real contradiction.
    NORMALIZED_EQUIVALENT = "normalized_equivalent"


@dataclass(frozen=True)
class CanonicalAttribute:
    """Workspace-level canonical attribute after deterministic confluence."""

    key: str
    canonical_name: str
    definition: str
    value_kind: str
    #: Normalized unit dimension for measure attributes; None otherwise.
    unit_dimension: str | None


@dataclass(frozen=True)
class ChannelItem:
    kind: ChannelKind
    subject_id: uuid.UUID
    subject_name: str
    reason: str
    fact_a_id: uuid.UUID
    fact_b_id: uuid.UUID
    predicate_a: str
    predicate_b: str
    value_a: object
    value_b: object
    source_a: str | None
    source_b: str | None
    canonical_name: str | None = None
    value_kind: str | None = None


@dataclass(frozen=True)
class _Binding:
    """One predicate's resolved place inside one subject shard."""

    canonical_key: str
    value_kind: str
    unit_dimension: str | None
    #: False when evidence/anchors could not be verified (§7-3): the merge
    #: is visible as pending review only, never as a trusted equivalence.
    trusted: bool


@dataclass(frozen=True)
class PairIdentity:
    """What the resolution stage knows about a fact pair's attribute identity.

    kind: 'A' literal-identical predicates | 'B' model-merged | None unrelated
    """

    kind: str | None
    trusted: bool
    value_kind: str | None
    canonical_name: str | None


@dataclass
class AttributeResolution:
    """Result of the attribute-resolution stage for one workspace."""

    #: Fact ids participating in the gray-release (pricing sources in
    #: phase 1). Only these facts receive identity-B / text-tightened rules;
    #: everything else keeps the pre-existing literal behavior.
    eligible_fact_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    enabled_source_types: frozenset[str] = field(default_factory=frozenset)
    bindings: dict[tuple[uuid.UUID, str], _Binding] = field(default_factory=dict)
    canonicals: dict[str, CanonicalAttribute] = field(default_factory=dict)
    #: Model-proposed text/enumeration value equivalences per subject,
    #: keyed by case-folded value pair (normalized-equivalent channel).
    text_equivalences: set[tuple[uuid.UUID, str, str]] = field(default_factory=set)
    #: Alias pairs the model proposed but deterministic anchor validation
    #: rejected (contradictory measure dimensions). Pairs here surface in the
    #: cross-attribute review channel instead of disappearing (§7-3, Golden 11).
    rejected_merges: set[tuple[uuid.UUID, str, str]] = field(default_factory=set)
    #: Non-fatal diagnostics (shard LLM failures, oversized shard splits…).
    notes: list[str] = field(default_factory=list)

    # -- queries used by the conflict detector -----------------------------

    def _binding(self, subject_id: uuid.UUID, predicate: str) -> _Binding | None:
        return self.bindings.get((subject_id, predicate.strip().casefold()))

    def identity_for_pair(
        self, a_subject: uuid.UUID, b_subject: uuid.UUID,
        a_predicate: str, b_predicate: str,
    ) -> tuple[str, _Binding | None]:
        """Return ('A'|'B'|None, binding-or-None) for a fact pair.

        * ``A`` — identical literal predicates (identity never touched by
          the model);
        * ``B`` — different literals, merged by the model into one canonical;
        * ``None`` — different literals not co-merged.
        """
        if a_subject != b_subject:
            return None, None
        if a_predicate.strip().casefold() == b_predicate.strip().casefold():
            return "A", None
        ba = self._binding(a_subject, a_predicate)
        bb = self._binding(b_subject, b_predicate)
        if ba is not None and bb is not None and ba.canonical_key == bb.canonical_key:
            return "B", ba
        return None, None

    def is_eligible(self, fact_id: uuid.UUID | None) -> bool:
        return fact_id in self.eligible_fact_ids

    def pair_identity(
        self, a_subject: uuid.UUID, b_subject: uuid.UUID,
        a_predicate: str, b_predicate: str,
    ) -> PairIdentity:
        kind, binding = self.identity_for_pair(
            a_subject, b_subject, a_predicate, b_predicate
        )
        if kind is None:
            return PairIdentity(None, False, None, None)
        if kind == "A":
            return PairIdentity("A", True, None, None)
        return PairIdentity(
            "B",
            trusted=bool(binding and binding.trusted),
            value_kind=binding.value_kind if binding else None,
            canonical_name=self.canonical_name_for(binding),
        )

    def is_rejected_merge(
        self, subject_id: uuid.UUID, predicate_a: str, predicate_b: str
    ) -> bool:
        a = predicate_a.strip().casefold()
        b = predicate_b.strip().casefold()
        return (
            (subject_id, a, b) in self.rejected_merges
            or (subject_id, b, a) in self.rejected_merges
        )

    def is_text_equivalent(
        self, subject_id: uuid.UUID, value_a: object, value_b: object
    ) -> bool:
        a = "".join(str(value_a).split()).casefold()
        b = "".join(str(value_b).split()).casefold()
        if not a or not b:
            return False
        return (subject_id, a, b) in self.text_equivalences or (
            subject_id,
            b,
            a,
        ) in self.text_equivalences

    def canonical_name_for(self, binding: _Binding | None) -> str | None:
        if binding is None:
            return None
        canonical = self.canonicals.get(binding.canonical_key)
        return canonical.canonical_name if canonical else None

    @staticmethod
    def confluence_key(
        canonical_name: str, value_kind: str, unit_dimension: str | None
    ) -> str:
        """Deterministic workspace-level merge key (§9.0, zero LLM)."""
        name = normalize_name(canonical_name)
        return f"{value_kind}|{unit_dimension or '-'}|{name}"
