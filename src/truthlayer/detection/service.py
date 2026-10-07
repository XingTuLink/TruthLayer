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
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from truthlayer.attributes import (
    AttributeResolver,
    ChannelItem,
    ChannelKind,
)
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
from truthlayer.providers.llm import LLMProvider

#: Bump on any detector rule change; recorded on every ScanRun (#15).
DETECTOR_VERSION = "drift-core-v8"


def _json_safe(value: Any) -> Any:
    """Coerce a drift ``detail`` payload into JSONB-serialisable plain data.

    ``KnowledgeState`` restores date-typed fact values to real ``date``
    objects so detectors can do window/age comparisons, and detector detail
    payloads embed those values verbatim (e.g. duplicate's shared ``object``,
    conflict's old/new values). psycopg's JSON adapter cannot serialise
    ``date``/``datetime``, so the invariant is enforced at the single JSONB
    exit rather than relying on every detector to call ``isoformat()``.
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


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
    #: Attribute-resolution audit channels (phase 1, in-process, never
    #: persisted, never part of ci.fail_on / exit codes).
    pending_review: list[ChannelItem] = field(default_factory=list)
    cross_attribute_review: list[ChannelItem] = field(default_factory=list)
    normalized_equivalent: list[ChannelItem] = field(default_factory=list)
    attribute_notes: list[str] = field(default_factory=list)
    attribute_prompt_version: str | None = None

    @property
    def total(self) -> int:
        return self.new_count + self.suppressed_count

    @property
    def review_total(self) -> int:
        return (
            len(self.pending_review)
            + len(self.cross_attribute_review)
            + len(self.normalized_equivalent)
        )

    def channel_counts(self) -> dict[str, int]:
        return {
            ChannelKind.PENDING_REVIEW.value: len(self.pending_review),
            ChannelKind.CROSS_ATTRIBUTE_REVIEW.value: len(
                self.cross_attribute_review
            ),
            ChannelKind.NORMALIZED_EQUIVALENT.value: len(
                self.normalized_equivalent
            ),
        }


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
        llm: LLMProvider | None = None,
        *,
        attribute_source_types: frozenset[str] | None = None,
    ) -> DetectionResult:
        state = load_knowledge_state(self.session, workspace_id)
        context = DetectionContext.from_config(self.config, self.as_of)

        # Attribute semantic resolution is an independent stage that needs the
        # cross-chunk, cross-document global view (extraction is per-chunk).
        # With no LLM configured (e.g. `truthlayer check`, offline harness)
        # detection behavior is byte-identical to the literal detector.
        resolution = None
        attribute_prompt_version: str | None = None
        if llm is not None:
            resolver = (
                AttributeResolver(
                    llm, enabled_source_types=attribute_source_types
                )
                if attribute_source_types is not None
                else AttributeResolver(llm)
            )
            resolution = resolver.resolve(state)
            attribute_prompt_version = resolver.prompt_version
            for detector in self.detectors:
                if isinstance(detector, ConflictDetector):
                    detector.attribute_resolution = resolution
                elif isinstance(detector, StaleDetector):
                    detector.attribute_resolution = resolution

        candidates: list[DriftCandidate] = []
        channel_items: list[ChannelItem] = []
        for detector in self.detectors:
            candidates.extend(detector.detect(state, context))
            if isinstance(detector, ConflictDetector):
                channel_items.extend(detector.channel_items)

        known = self._load_known_fingerprints(workspace_id)
        result = DetectionResult(
            attribute_prompt_version=attribute_prompt_version,
            attribute_notes=list(resolution.notes) if resolution else [],
        )
        for item in channel_items:
            if item.kind is ChannelKind.PENDING_REVIEW:
                result.pending_review.append(item)
            elif item.kind is ChannelKind.CROSS_ATTRIBUTE_REVIEW:
                result.cross_attribute_review.append(item)
            else:
                result.normalized_equivalent.append(item)

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
            detail_jsonb=_json_safe(
                {**candidate.detail, "fingerprint": fingerprint}
            ),
        )
        self.session.add(drift)
        self.session.flush()
        return drift
