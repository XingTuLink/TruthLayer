"""ConflictDetector (#19).

same subject + same predicate + different object + overlapping validity
=> conflict candidate — but only across different source documents and only
when no explicit supersession explains the disagreement (that case belongs
to Stale/Superseded detectors).

Predicate is assumed single-valued by default; multi-valued predicates can
be declared in config (#19). Values from the SAME document (tier tables,
enumerations) are never conflicts: drift is a cross-source concept.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

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


@dataclass
class ConflictDetector:
    name: str = NAME

    def detect(
        self,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> list[DriftCandidate]:
        groups: dict[tuple[uuid.UUID, str], list[FactView]] = defaultdict(list)
        for fact in state.facts:
            groups[(fact.subject_id, fact.predicate.casefold())].append(fact)

        candidates: list[DriftCandidate] = []
        for (subject_id, _), facts in groups.items():
            if len(facts) < 2:
                continue
            if context.is_multi_valued(facts[0].predicate):
                continue
            for i, older in enumerate(facts):
                for newer in facts[i + 1 :]:
                    candidate = self._compare_pair(
                        older, newer, state, context, subject_id
                    )
                    if candidate is not None:
                        candidates.append(candidate)
        return candidates

    # -- internals ----------------------------------------------------------

    def _compare_pair(
        self,
        a: FactView,
        b: FactView,
        state: KnowledgeState,
        context: DetectionContext,
        subject_id: uuid.UUID,
    ) -> DriftCandidate | None:
        if objects_equal(
            a.object_value, a.object_type, a.object_entity_id,
            b.object_value, b.object_type, b.object_entity_id,
        ):
            return None  # Canonical Fact with multiple evidence (#12).

        if not windows_overlap(
            a.valid_from, a.valid_to, b.valid_from, b.valid_to
        ):
            return None  # Sequential validity, not a conflict.

        doc_a = state.fact_document(a)
        doc_b = state.fact_document(b)

        # Same-document disagreement is structural prose (tiers/enums),
        # not cross-source drift.
        if doc_a is not None and doc_b is not None and doc_a.id == doc_b.id:
            return None

        # Explicit replacement explains the disagreement — not a conflict.
        for doc in (doc_a, doc_b):
            if doc is not None and state.newer_version_exists(doc.id):
                return None

        old_fact, new_fact = _order_old_new(a, b)
        old_doc = state.fact_document(old_fact)
        new_doc = state.fact_document(new_fact)

        severity = DEFAULT_SEVERITY
        if old_doc is not None:
            severity = max_severity(
                severity,
                context.severity_for_change(
                    old_doc.source_type, DEFAULT_SEVERITY
                ),
            )
        if new_doc is not None:
            severity = max_severity(
                severity,
                context.severity_for_change(
                    new_doc.source_type, DEFAULT_SEVERITY
                ),
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
            subject_entity_id=subject_id,
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
                str(subject_id),
                old_fact.predicate.casefold(),
                *sorted((str(old_fact.id), str(new_fact.id))),
            ),
        )


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
