"""DriftDetectionService — run all detectors and persist findings (#18).

Responsibilities:
* build KnowledgeState / DetectionContext;
* run the four deterministic detectors;
* suppress findings already known under any status (open/ignored/resolved)
  via stable fingerprints — the Remember loop: a resolved false positive
  must not be re-reported on every scan;
* persist Drift rows and update ScanRun detector_version / drift_count.

Flushes but never commits; the caller owns the transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from truthlayer.config import TruthLayerConfig
from truthlayer.db.orm import Drift, ScanRun
from truthlayer.detection.base import DriftDetector
from truthlayer.detection.candidates import DriftCandidate
from truthlayer.detection.conflict import ConflictDetector
from truthlayer.detection.context import DetectionContext
from truthlayer.detection.duplicate import DuplicateDetector
from truthlayer.detection.state import load_knowledge_state
from truthlayer.detection.stale import StaleDetector
from truthlayer.detection.superseded import SupersededDetector
from truthlayer.domain.enums import DriftType

#: Bump on any detector rule change; recorded on every ScanRun (#15).
DETECTOR_VERSION = "drift-core-v1"


@dataclass
class DriftSummaryItem:
    drift_type: str
    severity: str
    detail: dict
    is_new: bool
    id: uuid.UUID | None = None


@dataclass
class DetectionResult:
    detector_version: str = DETECTOR_VERSION
    new_count: int = 0
    suppressed_count: int = 0
    by_type: dict[str, int] = field(
        default_factory=lambda: {dt.value: 0 for dt in DriftType}
    )
    items: list[DriftSummaryItem] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.new_count + self.suppressed_count


def default_detectors() -> list[DriftDetector]:
    return [
        ConflictDetector(),
        StaleDetector(),
        SupersededDetector(),
        DuplicateDetector(),
    ]


class DriftDetectionService:
    def __init__(
        self,
        session: Session,
        config: TruthLayerConfig,
        detectors: list[DriftDetector] | None = None,
        as_of: date | None = None,
    ) -> None:
        self.session = session
        self.config = config
        self.detectors = detectors or default_detectors()
        self.as_of = as_of

    def run(
        self,
        workspace_id: uuid.UUID,
        scan_run: ScanRun,
    ) -> DetectionResult:
        state = load_knowledge_state(self.session, workspace_id)
        context = DetectionContext.from_config(self.config, self.as_of)

        candidates: list[DriftCandidate] = []
        for detector in self.detectors:
            candidates.extend(detector.detect(state, context))

        known = self._load_known_fingerprints(workspace_id)
        result = DetectionResult()

        # Deterministic order for stable outputs/tests.
        candidates.sort(key=lambda c: c.fingerprint())

        for candidate in candidates:
            fingerprint = candidate.fingerprint()
            if fingerprint in known:
                result.suppressed_count += 1
                result.items.append(
                    DriftSummaryItem(
                        drift_type=candidate.drift_type.value,
                        severity=candidate.severity.value,
                        detail=dict(candidate.detail),
                        is_new=False,
                    )
                )
                continue

            drift = self._persist(candidate, fingerprint, scan_run.id)
            known.add(fingerprint)
            result.new_count += 1
            result.by_type[candidate.drift_type.value] += 1
            result.items.append(
                DriftSummaryItem(
                    id=drift.id,
                    drift_type=candidate.drift_type.value,
                    severity=candidate.severity.value,
                    detail=dict(candidate.detail),
                    is_new=True,
                )
            )

        scan_run.detector_version = DETECTOR_VERSION
        scan_run.drift_count = result.new_count
        self.session.flush()
        return result

    # -- internals ----------------------------------------------------------

    def _load_known_fingerprints(self, workspace_id: uuid.UUID) -> set[str]:
        """Fingerprints of every drift ever recorded in this workspace.

        Any status suppresses re-reporting: open (still tracked), ignored
        and resolved (human decision remembered).
        """
        rows = self.session.execute(
            select(Drift.detail_jsonb["fingerprint"].astext)
            .join(ScanRun, Drift.scan_run_id == ScanRun.id)
            .where(ScanRun.workspace_id == workspace_id)
        ).all()
        return {row[0] for row in rows if row[0]}

    def _persist(
        self,
        candidate: DriftCandidate,
        fingerprint: str,
        scan_run_id: uuid.UUID,
    ) -> Drift:
        drift = Drift(
            scan_run_id=scan_run_id,
            target_type=candidate.target_type.value,
            target_id=candidate.target_id,
            type=candidate.drift_type.value,
            severity=candidate.severity.value,
            status="open",
            subject_entity_id=candidate.subject_entity_id,
            predicate=candidate.predicate,
            old_fact_id=candidate.old_fact_id,
            new_fact_id=candidate.new_fact_id,
            old_document_id=candidate.old_document_id,
            new_document_id=candidate.new_document_id,
            detector_type=candidate.detector_type,
            ai_impact_level=candidate.ai_impact_level.value,
            effective_at=candidate.effective_at,
            confidence=candidate.confidence,
            detail_jsonb={**candidate.detail, "fingerprint": fingerprint},
        )
        self.session.add(drift)
        self.session.flush()
        return drift
