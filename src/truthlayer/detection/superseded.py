"""SupersededDetector (#18, #8).

Document-level drift: a newer document EXPLICITLY declares an older one as
its previous version. Version-label sorting is never used here — the edge
must already exist from ingestion's explicit ``supersedes`` mapping.

A chain A -> B -> C yields one drift per replaced document (A by B, B by C).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from truthlayer.detection.candidates import DriftCandidate
from truthlayer.detection.context import DetectionContext
from truthlayer.detection.scoring import (
    STRUCTURAL_CONFIDENCE,
    ai_impact_for,
)
from truthlayer.detection.state import DocumentView, KnowledgeState
from truthlayer.domain.enums import DriftType, Severity, TargetType

NAME = "superseded_detector"
DEFAULT_SEVERITY = Severity.MEDIUM


@dataclass
class SupersededDetector:
    name: str = NAME

    def detect(
        self,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> list[DriftCandidate]:
        successors = self._successor_index(state)
        candidates: list[DriftCandidate] = []
        for old_id, new_doc in successors.items():
            old_doc = state.documents[old_id]
            severity = context.severity_for_change(
                old_doc.source_type, DEFAULT_SEVERITY
            )
            candidates.append(
                DriftCandidate(
                    detector_type=NAME,
                    drift_type=DriftType.SUPERSEDED,
                    target_type=TargetType.DOCUMENT,
                    target_id=old_doc.id,
                    severity=severity,
                    ai_impact_level=ai_impact_for(severity),
                    confidence=STRUCTURAL_CONFIDENCE,
                    old_document_id=old_doc.id,
                    new_document_id=new_doc.id,
                    effective_at=new_doc.parsed_at,
                    detail={
                        "reason": "explicit_version_replacement",
                        "old_source": old_doc.filename,
                        "new_source": new_doc.filename,
                        "group_id": str(old_doc.group_id)
                        if old_doc.group_id
                        else None,
                    },
                    fingerprint_key=(
                        DriftType.SUPERSEDED.value,
                        str(old_doc.id),
                        str(new_doc.id),
                    ),
                )
            )
        return candidates

    @staticmethod
    def _successor_index(
        state: KnowledgeState,
    ) -> dict[uuid.UUID, DocumentView]:
        """old document id -> its (first, deterministic) declared successor."""
        successors: dict[uuid.UUID, DocumentView] = {}
        for doc in sorted(state.documents.values(), key=lambda d: str(d.id)):
            if (
                doc.previous_version_id is not None
                and doc.previous_version_id in state.documents
                and doc.previous_version_id not in successors
            ):
                successors[doc.previous_version_id] = doc
        return successors
