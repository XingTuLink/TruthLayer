"""StaleDetector (#20).

Three layers are kept strictly separate:

* Source Age          — information only, never an alert;
* possibly_stale      — older than the configured threshold but no proof of
                        invalidation (heuristic, low confidence);
* confirmed_stale     — valid_to expired, or an explicit superseding source
                        exists (deterministic, high confidence).

Document old does NOT mean knowledge old: facts without any age signal
(observed_at / valid_from) are never guessed stale. Edition metadata does
not mean knowledge drift either: immutable document-identity predicates
(document numbers, edition labels, effective/repeal dates, authoring
stamps, product codes) stay true about their own edition forever, so they
never raise confirmed_stale or possibly_stale (configurable via
``rules.immutable_metadata_predicates``).

R16 (design 04 §11) adds one more deterministic, model-free path: a
*current* document quotes a superseded edition's value verbatim while the
version-chain head already carries a different value. Four constraints must
all hold — same subject, same measure dimension, the old fact sits on an
explicit supersedes chain, numbers compare strictly equal after
normalization, and the chain-head value differs — so bare value collisions
across subjects/units and legitimate flat pricings stay silent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from truthlayer.attributes.anchors import normalize_dimension, normalize_number
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
#: Quoting a dead value inside a *current* document actively pollutes live
#: answers, so R16 defaults one notch above plain confirmed_stale.
DEFAULT_REUSED_SEVERITY = Severity.HIGH


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
        candidates.extend(self._detect_reused_superseded_values(state, context))
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

        # Immutable edition/identity metadata is historically true about its
        # own document regardless of expiry or supersession — never a stale
        # alert on either the deterministic or the age-only path.
        if context.is_immutable_metadata(fact.predicate):
            return None

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

    # -- R16: verbatim reuse of a superseded value --------------------------

    def _detect_reused_superseded_values(
        self,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> list[DriftCandidate]:
        candidates: list[DriftCandidate] = []
        for fact in state.facts:
            current_doc = state.fact_document(fact)
            if current_doc is None:
                continue
            # The quoting document must itself be a *live* source: facts on
            # superseded editions are handled by confirmed_stale above.
            if state.newer_version_exists(current_doc.id):
                continue
            if not self._window_is_current(fact, context.as_of):
                continue
            if fact.object_type != "number":
                continue
            current_number = normalize_number(fact.object_value)
            current_dimension = normalize_dimension(fact.measure_unit or "")
            # The measure unit is the hard, model-free attribute anchor;
            # without it cross-attribute value collisions cannot be ruled out.
            if current_number is None or not current_dimension:
                continue

            match = self._match_superseded_reuse(
                state,
                current_fact=fact,
                current_number=current_number,
                current_dimension=current_dimension,
            )
            if match is None:
                continue
            old_fact, old_doc, head_doc, head_fact = match
            severity = context.severity_for_change(
                current_doc.source_type, DEFAULT_REUSED_SEVERITY
            )
            candidates.append(
                DriftCandidate(
                    detector_type=NAME,
                    drift_type=DriftType.REUSED_STALE_VALUE,
                    target_type=TargetType.FACT,
                    target_id=fact.id,
                    severity=severity,
                    ai_impact_level=ai_impact_for(severity),
                    confidence=STRUCTURAL_CONFIDENCE,
                    subject_entity_id=fact.subject_id,
                    predicate=fact.predicate,
                    old_fact_id=fact.id,
                    new_fact_id=head_fact.id,
                    old_document_id=old_doc.id,
                    new_document_id=head_doc.id,
                    effective_at=at_utc_midday(fact.valid_from),
                    detail={
                        "reason": "reused_superseded_value",
                        "subject": fact.subject_name,
                        "predicate": fact.predicate,
                        "reused_value": fact.object_value,
                        "head_value": head_fact.object_value,
                        "unit": fact.measure_unit,
                        "source": current_doc.filename,
                        "old_source": old_doc.filename,
                        "new_source": head_doc.filename,
                    },
                    fingerprint_key=(
                        DriftType.REUSED_STALE_VALUE.value,
                        str(fact.id),
                    ),
                )
            )
        return candidates

    @staticmethod
    def _window_is_current(fact: FactView, as_of) -> bool:
        if fact.valid_to is not None and fact.valid_to < as_of:
            return False
        if fact.valid_from is not None and fact.valid_from > as_of:
            return False
        return True

    def _match_superseded_reuse(
        self,
        state: KnowledgeState,
        *,
        current_fact: FactView,
        current_number,
        current_dimension: str,
    ) -> tuple[FactView, DocumentView, DocumentView, FactView] | None:
        """Find the closest superseded-edition fact carrying the same value.

        Returns ``(old_fact, old_doc, head_doc, head_fact)`` or ``None`` when
        any of the four R16 constraints fails. When several old editions
        contain the value, the edition nearest the chain head wins (the most
        recent dead value is what the current document most plausibly copied).
        """
        best: tuple[tuple[int, str], FactView, DocumentView, DocumentView, FactView] | None = None
        current_doc = state.fact_document(current_fact)
        for old_fact in state.facts:
            if old_fact.id == current_fact.id:
                continue
            if old_fact.subject_id != current_fact.subject_id:
                continue
            if old_fact.object_type != "number":
                continue
            if normalize_number(old_fact.object_value) != current_number:
                continue
            old_dimension = normalize_dimension(old_fact.measure_unit or "")
            if not old_dimension or old_dimension != current_dimension:
                continue
            old_doc = state.fact_document(old_fact)
            if old_doc is None or current_doc is None:
                continue
            if old_doc.id == current_doc.id:
                continue
            # Constraint 2: the copied fact must sit on an explicit
            # supersedes chain — never inferred from dates or filenames.
            if not state.newer_version_exists(old_doc.id):
                continue
            if old_doc.group_id is None:
                continue
            head_doc = self._chain_head(state, old_doc)
            if head_doc is None:
                continue
            head_fact = self._head_current_value(
                state,
                subject_id=current_fact.subject_id,
                dimension=current_dimension,
                current_number=current_number,
                head_doc=head_doc,
            )
            if head_fact is None:
                continue
            distance = self._chain_distance(state, head_doc, old_doc)
            if distance is None:
                continue
            ranking = (distance, str(old_fact.id))
            if best is None or ranking < best[0]:
                best = (ranking, old_fact, old_doc, head_doc, head_fact)
        if best is None:
            return None
        return best[1], best[2], best[3], best[4]

    @staticmethod
    def _chain_head(
        state: KnowledgeState, old_doc: DocumentView
    ) -> DocumentView | None:
        """The unique group member that no other document declares as old."""
        heads = [
            doc
            for doc in state.documents.values()
            if doc.group_id == old_doc.group_id
            and not state.newer_version_exists(doc.id)
        ]
        return heads[0] if len(heads) == 1 else None

    @staticmethod
    def _chain_distance(
        state: KnowledgeState, head_doc: DocumentView, old_doc: DocumentView
    ) -> int | None:
        """Steps from the head down the ``previous_version_id`` chain."""
        distance = 0
        current: DocumentView | None = head_doc
        while current is not None:
            if current.id == old_doc.id:
                return distance
            previous_id = current.previous_version_id
            current = (
                state.documents.get(previous_id) if previous_id is not None else None
            )
            distance += 1
        return None

    @staticmethod
    def _head_current_value(
        state: KnowledgeState,
        *,
        subject_id: uuid.UUID,
        dimension: str,
        current_number,
        head_doc: DocumentView,
    ) -> FactView | None:
        """Same-subject/same-unit head fact proving the current value changed.

        ``None`` when the head has no comparable fact (constraint 4 cannot be
        verified — stay silent) or when any head fact still carries the same
        number (legitimate flat pricing across editions — must not alert).
        """
        same_anchor: list[FactView] = []
        for fact in state.facts:
            if fact.document_id != head_doc.id:
                continue
            if fact.subject_id != subject_id or fact.object_type != "number":
                continue
            if normalize_dimension(fact.measure_unit or "") != dimension:
                continue
            same_anchor.append(fact)
        if not same_anchor:
            return None
        for fact in same_anchor:
            if normalize_number(fact.object_value) == current_number:
                return None
        return sorted(same_anchor, key=lambda f: str(f.id))[0]
