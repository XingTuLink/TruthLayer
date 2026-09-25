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
