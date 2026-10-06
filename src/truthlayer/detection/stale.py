"""StaleDetector (#20).

Three layers are kept strictly separate:

* Source Age          — information only, never an alert;
* possibly_stale      — older than the configured threshold but no proof of
                        invalidation (heuristic, low confidence);
* confirmed_stale     — valid_to expired, or an explicit superseding source
                        exists (deterministic, high confidence).

Document old does NOT mean knowledge old: facts without any age signal
(observed_at / valid_from) are never guessed stale.
"""

from __future__ import annotations

from dataclasses import dataclass

from truthlayer.detection.candidates import DriftCandidate
from truthlayer.detection.context import DetectionContext
from truthlayer.detection.scoring import (
    HEURISTIC_CONFIDENCE,
    STRUCTURAL_CONFIDENCE,
    ai_impact_for,
    at_utc_midday,
)
from truthlayer.detection.state import DocumentView, FactView, KnowledgeState
from truthlayer.domain.enums import DriftType, Severity, TargetType

NAME = "stale_detector"
DEFAULT_CONFIRMED_SEVERITY = Severity.MEDIUM


@dataclass
class StaleDetector:
    name: str = NAME

    def detect(
        self,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> list[DriftCandidate]:
        candidates: list[DriftCandidate] = []
        for fact in state.facts:
            document = state.fact_document(fact)
            candidate = self._evaluate(fact, document, state, context)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    # -- internals ----------------------------------------------------------

    def _evaluate(
        self,
        fact: FactView,
        document: DocumentView | None,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> DriftCandidate | None:
        signals: list[str] = []
        newer_doc: DocumentView | None = None

        if fact.valid_to is not None and fact.valid_to < context.as_of:
            signals.append("valid_to_expired")

        if document is not None:
            for doc in state.documents.values():
                if doc.previous_version_id == document.id:
                    signals.append("superseding_source")
                    newer_doc = doc
                    break

        if signals:
            severity = DEFAULT_CONFIRMED_SEVERITY
            if document is not None:
                severity = context.severity_for_change(
                    document.source_type, DEFAULT_CONFIRMED_SEVERITY
                )
            return DriftCandidate(
                detector_type=NAME,
                drift_type=DriftType.CONFIRMED_STALE,
                target_type=TargetType.FACT,
                target_id=fact.id,
                severity=severity,
                ai_impact_level=ai_impact_for(severity),
                confidence=STRUCTURAL_CONFIDENCE,
                subject_entity_id=fact.subject_id,
                predicate=fact.predicate,
                old_fact_id=fact.id,
                old_document_id=document.id if document else None,
                new_document_id=newer_doc.id if newer_doc else None,
                effective_at=(
                    at_utc_midday(fact.valid_to)
                    if "valid_to_expired" in signals
                    else (newer_doc.parsed_at if newer_doc else None)
                ),
                detail={
                    "reason": "+".join(signals),
                    "subject": fact.subject_name,
                    "predicate": fact.predicate,
                    "source": document.filename if document else None,
                    "new_source": newer_doc.filename if newer_doc else None,
                    "valid_to": fact.valid_to.isoformat()
                    if fact.valid_to
                    else None,
                },
                fingerprint_key=(
                    DriftType.CONFIRMED_STALE.value,
                    str(fact.id),
                ),
            )

        # Possible stale: age heuristic only, and only with a real signal.
        age_basis = fact.age_basis
        if age_basis is None:
            return None

        threshold_days = context.stale_after_days
        if document is not None and document.source_type == "pricing":
            threshold_days = context.pricing_stale_days

        age_days = (context.as_of - age_basis).days
        if age_days <= threshold_days:
            return None

        # Bare age is only a "nobody re-verified this" nudge. Two benign
        # shapes (current chain edition / open-ended annual rate still in its
        # edition year) are not invalidation and must stay silent.
        if self._age_only_suppressed(fact, document, state, context):
            return None

        return DriftCandidate(
            detector_type=NAME,
            drift_type=DriftType.POSSIBLY_STALE,
            target_type=TargetType.FACT,
            target_id=fact.id,
            severity=Severity.WARNING,
            ai_impact_level=ai_impact_for(Severity.WARNING),
            confidence=HEURISTIC_CONFIDENCE,
            subject_entity_id=fact.subject_id,
            predicate=fact.predicate,
            old_fact_id=fact.id,
            old_document_id=document.id if document else None,
            effective_at=None,
            detail={
                "reason": "age_over_threshold",
                "subject": fact.subject_name,
                "predicate": fact.predicate,
                "source": document.filename if document else None,
                "age_days": age_days,
                "threshold_days": threshold_days,
                "age_basis": age_basis.isoformat(),
            },
            fingerprint_key=(
                DriftType.POSSIBLY_STALE.value,
                str(fact.id),
            ),
        )

    def _age_only_suppressed(
        self,
        fact: FactView,
        document: DocumentView | None,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> bool:
        """Whether a bare-age possibly-stale nudge should stay silent.

        Only reached when the fact is over the age threshold and carries no
        deterministic invalidation (no expired ``valid_to``, no superseding
        source). Two shapes are inherently "still current":

        * **Current edition** — the fact's document belongs to an explicit
          version chain and is its newest member. It is the authoritative
          current statement until a later edition replaces it; the age of the
          current edition is not invalidation.

        * **Open-ended annual rate** — a pricing fact explicitly in effect
          from the start of a year (YYYY-01-01) with no expiry date is the
          rate for that edition year. It only becomes review-worthy after
          that year closes and the pricing review threshold elapses. A
          mid-year change (e.g. a limited campaign) is not covered and still
          ages normally.
        """
        # A bounded fact (any explicit validity end) keeps the normal logic.
        if fact.valid_to is not None:
            return False

        if document is not None and document.group_id is not None:
            if not state.newer_version_exists(document.id):
                return True

        if (
            document is not None
            and document.source_type == "pricing"
            and fact.valid_from is not None
            and (fact.valid_from.month, fact.valid_from.day) == (1, 1)
            and fact.valid_from.year == context.as_of.year
        ):
            return True

        return False
