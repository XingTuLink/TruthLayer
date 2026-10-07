"""ConflictDetector (#19 + attribute resolution phase 1).

same subject + same *attribute* + different value + overlapping validity
=> a disagreement across source documents, but only when no explicit
supersession explains it (that case belongs to Stale/Superseded).

Two identity layers decide what happens next (design 04 §5-8):

* literal predicate equality (identity A) — the deterministic path; measure
  facts are compared through structured anchors (value/unit/currency/tax),
  structured equality suppresses, structured contradiction convicts;
* model-merged aliases (identity B) — same hard gates, but the output is a
  non-blocking *pending review* item regardless of anchors. The LLM never
  convicts.

Pairs that look structurally comparable but were never co-merged land in the
structural *cross-attribute review* channel. None of the three audit channels
affects ci.fail_on / exit codes.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from truthlayer.attributes import (
    AttributeResolution,
    ChannelItem,
    ChannelKind,
    measure_compare,
    normalize_dimension,
    normalize_number,
)
from truthlayer.detection.candidates import (
    DriftCandidate,
    objects_equal,
    windows_overlap,
)
from truthlayer.detection.context import DetectionContext
from truthlayer.detection.scoring import (
    STRUCTURAL_CONFIDENCE,
    ai_impact_for,
    at_utc_midday,
    max_severity,
)
from truthlayer.detection.state import FactView, KnowledgeState
from truthlayer.domain.enums import DriftType, Severity, TargetType

NAME = "conflict_detector"
DEFAULT_SEVERITY = Severity.HIGH

#: A string predicate whose values are short, few and repeatable is treated
#: as an enumeration under identity A (deterministic, vocabulary-free); long
#: free-text values are tightened to pending review inside the gray release
#: (design §5.1-4, §8).
ENUM_MAX_VALUE_LEN = 20
ENUM_MAX_DISTINCT = 8


@dataclass
class ConflictDetector:
    name: str = NAME
    #: When None, the detector behaves exactly as the pre-attribute literal
    #: detector (used by the offline Golden harness).
    attribute_resolution: AttributeResolution | None = None
    channel_items: list[ChannelItem] = field(default_factory=list, init=False)

    def detect(
        self,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> list[DriftCandidate]:
        self.channel_items = []
        resolution = self.attribute_resolution

        groups: dict[tuple[uuid.UUID, str], list[FactView]] = defaultdict(list)
        for fact in state.facts:
            groups[(fact.subject_id, fact.predicate.casefold())].append(fact)

        candidates: list[DriftCandidate] = []
        for (_subject_id, predicate_key), facts in groups.items():
            if len(facts) < 2:
                continue
            if context.is_multi_valued(facts[0].predicate):
                continue
            for i, older in enumerate(facts):
                for newer in facts[i + 1 :]:
                    candidate = self._compare_literal_pair(
                        older, newer, state, context, predicate_key
                    )
                    if candidate is not None:
                        candidates.append(candidate)

        if resolution is not None:
            self._run_attribute_channels(state, context)

        return candidates

    # -- identity A: literal predicate groups -------------------------------

    def _compare_literal_pair(
        self,
        a: FactView,
        b: FactView,
        state: KnowledgeState,
        context: DetectionContext,
        predicate_key: str,
    ) -> DriftCandidate | None:
        resolution = self.attribute_resolution
        gated = self._structural_gates(a, b, state, context, {a.predicate})
        if gated is None:
            return None
        old_fact, new_fact = gated

        measure = measure_compare(
            a_value=a.object_value,
            a_type=a.object_type,
            a_unit=a.measure_unit,
            a_currency=a.currency,
            a_tax=a.tax_basis,
            b_value=b.object_value,
            b_type=b.object_type,
            b_unit=b.measure_unit,
            b_currency=b.currency,
            b_tax=b.tax_basis,
        )

        if (
            resolution is not None
            and resolution.is_eligible(a.id)
            and resolution.is_eligible(b.id)
            and measure.verdict.value != "not_measure"
        ):
            # Structured-measure path (§5.1).
            if measure.verdict.value == "consistent" and measure.same_value:
                return None  # deterministic canonical fact, no model involved
            if measure.verdict.value == "consistent" and not measure.same_value:
                return self._conflict_candidate(
                    old_fact, new_fact, state, context
                )
            if measure.verdict.value == "missing":
                # T-02: anchors unparsed/uncertain — never silently canonical,
                # never a conviction. Equal values still ask a human to
                # confirm comparability (e.g. one side's tax basis unknown).
                self._channel(
                    ChannelKind.PENDING_REVIEW,
                    old_fact,
                    new_fact,
                    state,
                    reason="measure_anchor_missing",
                )
                return None
            # anchor conflict (unit/currency): never the same comparable thing
            self._channel(
                ChannelKind.CROSS_ATTRIBUTE_REVIEW,
                old_fact,
                new_fact,
                state,
                reason="measure_anchor_conflict",
            )
            return None

        # Legacy path (no resolution or non-measure facts).
        if objects_equal(
            a.object_value, a.object_type, a.object_entity_id,
            b.object_value, b.object_type, b.object_entity_id,
        ):
            return None  # Canonical Fact with multiple evidence (#12).

        if (
            resolution is not None
            and resolution.is_eligible(a.id)
            and resolution.is_eligible(b.id)
            and self._is_free_text(state, a.subject_id, predicate_key)
        ):
            # A × text tightened (§5.1-4): literal-different prose values are
            # usually paraphrases; they ask a human instead of convicting.
            self._channel(
                ChannelKind.PENDING_REVIEW,
                old_fact,
                new_fact,
                state,
                reason="literal_text_difference",
            )
            return None

        return self._conflict_candidate(old_fact, new_fact, state, context)

    # -- identity B + structural audit channels -----------------------------

    def _run_attribute_channels(
        self,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> None:
        resolution = self.attribute_resolution
        assert resolution is not None
        seen_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()

        # Identity B: facts whose predicates the model merged into one canonical.
        by_subject: dict[uuid.UUID, list[FactView]] = defaultdict(list)
        for fact in state.facts:
            if resolution.is_eligible(fact.id):
                by_subject[fact.subject_id].append(fact)

        for subject_id, facts in by_subject.items():
            canonical_groups: dict[str, list[FactView]] = defaultdict(list)
            for fact in facts:
                binding = resolution.bindings.get(
                    (subject_id, fact.predicate.strip().casefold())
                )
                if binding is not None:
                    canonical_groups[binding.canonical_key].append(fact)

            for canonical_key, group_facts in canonical_groups.items():
                folds = {f.predicate.strip().casefold() for f in group_facts}
                if len(folds) < 2:
                    continue
                predicates = {f.predicate for f in group_facts}
                if any(context.is_multi_valued(p) for p in predicates):
                    continue
                binding = resolution.bindings[
                    (subject_id, next(iter(folds)))
                ]
                for i, a in enumerate(group_facts):
                    for b in group_facts[i + 1:]:
                        if a.predicate.strip().casefold() == b.predicate.strip().casefold():
                            continue  # same-literal pairs handled in path A
                        self._evaluate_merged_pair(
                            a, b, state, context, resolution, binding, seen_pairs
                        )

            # Structural cross-attribute scan: any two eligible facts with
            # different literal predicates, same parseable unit, different
            # numbers, overlapping windows, cross-document — whether or not
            # the model ever saw them together (design §10).
            self._structural_cross_scan(
                facts, state, context, resolution, seen_pairs
            )

    def _evaluate_merged_pair(
        self,
        a: FactView,
        b: FactView,
        state: KnowledgeState,
        context: DetectionContext,
        resolution: AttributeResolution,
        binding,
        seen_pairs: set[tuple[uuid.UUID, uuid.UUID]],
    ) -> None:
        gated = self._structural_gates(
            a, b, state, context, {a.predicate, b.predicate}
        )
        if gated is None:
            return
        old_fact, new_fact = gated
        pair_key = self._pair_key(old_fact.id, new_fact.id)
        seen_pairs.add(pair_key)

        identity = resolution.pair_identity(
            old_fact.subject_id,
            new_fact.subject_id,
            old_fact.predicate,
            new_fact.predicate,
        )
        measure = measure_compare(
            a_value=old_fact.object_value,
            a_type=old_fact.object_type,
            a_unit=old_fact.measure_unit,
            a_currency=old_fact.currency,
            a_tax=old_fact.tax_basis,
            b_value=new_fact.object_value,
            b_type=new_fact.object_type,
            b_unit=new_fact.measure_unit,
            b_currency=new_fact.currency,
            b_tax=new_fact.tax_basis,
        )

        if measure.verdict.value == "not_measure":
            if objects_equal(
                old_fact.object_value,
                old_fact.object_type,
                old_fact.object_entity_id,
                new_fact.object_value,
                new_fact.object_type,
                new_fact.object_entity_id,
            ):
                self._channel(
                    ChannelKind.NORMALIZED_EQUIVALENT,
                    old_fact,
                    new_fact,
                    state,
                    reason="merged_attribute_equal_value",
                    canonical=identity.canonical_name,
                    value_kind=binding.value_kind,
                )
                return
            if binding.value_kind in {"text", "enumeration"} and (
                resolution.is_text_equivalent(
                    old_fact.subject_id,
                    str(old_fact.object_value),
                    str(new_fact.object_value),
                )
            ):
                self._channel(
                    ChannelKind.NORMALIZED_EQUIVALENT,
                    old_fact,
                    new_fact,
                    state,
                    reason="model_text_equivalence",
                    canonical=identity.canonical_name,
                    value_kind=binding.value_kind,
                )
                return
            self._channel(
                ChannelKind.PENDING_REVIEW,
                old_fact,
                new_fact,
                state,
                reason="merged_attribute_value_differs",
                canonical=identity.canonical_name,
                value_kind=binding.value_kind,
            )
            return

        if measure.verdict.value == "consistent" and measure.same_value:
            self._channel(
                ChannelKind.NORMALIZED_EQUIVALENT,
                old_fact,
                new_fact,
                state,
                reason="merged_measure_equal_value",
                canonical=identity.canonical_name,
                value_kind=binding.value_kind,
            )
            return

        if measure.verdict.value == "conflict":
            self._channel(
                ChannelKind.CROSS_ATTRIBUTE_REVIEW,
                old_fact,
                new_fact,
                state,
                reason="merged_pair_anchor_conflict",
                canonical=identity.canonical_name,
                value_kind=binding.value_kind,
            )
            return

        # consistent-but-different (trusted or not) and anchor-missing both
        # become pending: identity B can never convict (§6).
        self._channel(
            ChannelKind.PENDING_REVIEW,
            old_fact,
            new_fact,
            state,
            reason=(
                "model_merged_unconfirmed"
                if identity.trusted and measure.verdict.value == "consistent"
                else "merged_pair_anchor_unverified"
            ),
            canonical=identity.canonical_name,
            value_kind=binding.value_kind,
        )

    def _structural_cross_scan(
        self,
        facts: list[FactView],
        state: KnowledgeState,
        context: DetectionContext,
        resolution: AttributeResolution,
        seen_pairs: set[tuple[uuid.UUID, uuid.UUID]],
    ) -> None:
        measure_facts = [
            f
            for f in facts
            if f.object_type == "number"
            and normalize_dimension(f.measure_unit) is not None
            and normalize_number(f.object_value) is not None
        ]
        for i, a in enumerate(measure_facts):
            for b in measure_facts[i + 1 :]:
                if a.predicate.strip().casefold() == b.predicate.strip().casefold():
                    continue
                identity = resolution.pair_identity(
                    a.subject_id, b.subject_id, a.predicate, b.predicate
                )
                if identity.kind == "B":
                    continue  # handled (or will be) by the merged path
                gated = self._structural_gates(
                    a, b, state, context, {a.predicate, b.predicate}
                )
                if gated is None:
                    continue
                old_fact, new_fact = gated
                pair_key = self._pair_key(old_fact.id, new_fact.id)
                if pair_key in seen_pairs:
                    continue
                measure = measure_compare(
                    a_value=old_fact.object_value,
                    a_type=old_fact.object_type,
                    a_unit=old_fact.measure_unit,
                    a_currency=old_fact.currency,
                    a_tax=old_fact.tax_basis,
                    b_value=new_fact.object_value,
                    b_type=new_fact.object_type,
                    b_unit=new_fact.measure_unit,
                    b_currency=new_fact.currency,
                    b_tax=new_fact.tax_basis,
                )
                if measure.verdict.value == "conflict":
                    if resolution.is_rejected_merge(
                        old_fact.subject_id,
                        old_fact.predicate,
                        new_fact.predicate,
                    ):
                        seen_pairs.add(pair_key)
                        self._channel(
                            ChannelKind.CROSS_ATTRIBUTE_REVIEW,
                            old_fact,
                            new_fact,
                            state,
                            reason="measure_anchor_conflict_split",
                        )
                    continue
                if measure.verdict.value not in {"consistent", "missing"}:
                    continue
                if measure.same_value:
                    continue
                seen_pairs.add(pair_key)
                self._channel(
                    ChannelKind.CROSS_ATTRIBUTE_REVIEW,
                    old_fact,
                    new_fact,
                    state,
                    reason="same_unit_different_value_not_comerged",
                )

    # -- shared helpers ------------------------------------------------------

    def _structural_gates(
        self,
        a: FactView,
        b: FactView,
        state: KnowledgeState,
        context: DetectionContext,
        predicates: set[str],
    ) -> tuple[FactView, FactView] | None:
        """The shared necessary gates (design §6). Returns ordered facts."""
        if not windows_overlap(
            a.valid_from, a.valid_to, b.valid_from, b.valid_to
        ):
            return None
        doc_a = state.fact_document(a)
        doc_b = state.fact_document(b)
        if doc_a is not None and doc_b is not None and doc_a.id == doc_b.id:
            return None
        for doc in (doc_a, doc_b):
            if doc is not None and state.newer_version_exists(doc.id):
                return None
        if any(context.is_multi_valued(p) for p in predicates):
            return None
        old_fact, new_fact = _order_old_new(a, b)
        return old_fact, new_fact

    def _conflict_candidate(
        self,
        old_fact: FactView,
        new_fact: FactView,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> DriftCandidate:
        old_doc = state.fact_document(old_fact)
        new_doc = state.fact_document(new_fact)

        severity = DEFAULT_SEVERITY
        if old_doc is not None:
            severity = max_severity(
                severity,
                context.severity_for_change(old_doc.source_type, DEFAULT_SEVERITY),
            )
        if new_doc is not None:
            severity = max_severity(
                severity,
                context.severity_for_change(new_doc.source_type, DEFAULT_SEVERITY),
            )

        authority_min = min(
            (d.authority_score for d in (old_doc, new_doc) if d is not None),
            default=STRUCTURAL_CONFIDENCE,
        )

        return DriftCandidate(
            detector_type=NAME,
            drift_type=DriftType.CONFLICT,
            target_type=TargetType.FACT,
            target_id=old_fact.id,
            severity=severity,
            ai_impact_level=ai_impact_for(severity),
            confidence=round(min(authority_min, STRUCTURAL_CONFIDENCE), 2),
            subject_entity_id=old_fact.subject_id,
            predicate=old_fact.predicate,
            old_fact_id=old_fact.id,
            new_fact_id=new_fact.id,
            old_document_id=old_doc.id if old_doc else None,
            new_document_id=new_doc.id if new_doc else None,
            effective_at=at_utc_midday(new_fact.age_basis),
            detail={
                "reason": "overlapping_validity_different_objects",
                "subject": old_fact.subject_name,
                "predicate": old_fact.predicate,
                "old_value": _describe_object(old_fact),
                "new_value": _describe_object(new_fact),
                "old_source": old_doc.filename if old_doc else None,
                "new_source": new_doc.filename if new_doc else None,
            },
            fingerprint_key=(
                DriftType.CONFLICT.value,
                str(old_fact.subject_id),
                old_fact.predicate.casefold(),
                *sorted((str(old_fact.id), str(new_fact.id))),
            ),
        )

    def _channel(
        self,
        kind: ChannelKind,
        old_fact: FactView,
        new_fact: FactView,
        state: KnowledgeState,
        *,
        reason: str,
        canonical: str | None = None,
        value_kind: str | None = None,
    ) -> None:
        old_doc = state.fact_document(old_fact)
        new_doc = state.fact_document(new_fact)
        self.channel_items.append(
            ChannelItem(
                kind=kind,
                subject_id=old_fact.subject_id,
                subject_name=old_fact.subject_name,
                reason=reason,
                fact_a_id=old_fact.id,
                fact_b_id=new_fact.id,
                predicate_a=old_fact.predicate,
                predicate_b=new_fact.predicate,
                value_a=_describe_object(old_fact),
                value_b=_describe_object(new_fact),
                source_a=old_doc.filename if old_doc else None,
                source_b=new_doc.filename if new_doc else None,
                canonical_name=canonical,
                value_kind=value_kind,
            )
        )

    @staticmethod
    def _pair_key(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        return tuple(sorted((a, b)))  # type: ignore[return-value]

    def _is_free_text(
        self,
        state: KnowledgeState,
        subject_id: uuid.UUID,
        predicate_key: str,
    ) -> bool:
        """Deterministic text/enumeration heuristic for identity A (§8).

        Free-text = at least one string value is long prose OR the predicate
        takes an open-ended set of distinct values across this subject.
        """
        values = {
            f.object_value
            for f in state.facts
            if f.subject_id == subject_id
            and f.predicate.casefold() == predicate_key
            and f.object_type == "string"
            and isinstance(f.object_value, str)
        }
        if not values:
            return False
        if any(len(v.strip()) > ENUM_MAX_VALUE_LEN for v in values):
            return True
        return len(values) > ENUM_MAX_DISTINCT


def _order_old_new(a: FactView, b: FactView) -> tuple[FactView, FactView]:
    """Earlier statement first; tie-break deterministically by id."""
    a_basis = a.age_basis
    b_basis = b.age_basis
    if a_basis is not None and b_basis is not None and a_basis != b_basis:
        return (a, b) if a_basis < b_basis else (b, a)
    if a_basis is not None and b_basis is None:
        return b, a  # undated statement treated as potentially newer
    if b_basis is not None and a_basis is None:
        return a, b
    return (a, b) if str(a.id) <= str(b.id) else (b, a)


def _describe_object(fact: FactView) -> str | float | int | bool | None:
    if fact.object_entity_id is not None:
        return fact.object_entity_name
    return fact.object_value
