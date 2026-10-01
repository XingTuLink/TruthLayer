"""ResolutionService — human decisions on drift (#27).

Phase 0 scope is strictly ``single``: one decision per drift, no pattern
engine. Resolving writes a Resolution row and flips drift.status to
``resolved``; ``ignore`` only sets status ``ignored``. The Remember loop in
detection treats both statuses as fingerprints that must never re-fire.

Flushes but never commits; the CLI owns the transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from truthlayer.db.orm import Drift, Entity, Resolution, ScanRun, Workspace
from truthlayer.domain.enums import (
    SEVERITY_RANK,
    DriftStatus,
    ReasonCode,
    ResolutionDecision,
    ResolutionScope,
    Severity,
)
from truthlayer.domain.errors import DomainValidationError, UserInputError


@dataclass(frozen=True)
class DriftListRecord:
    drift: Drift
    workspace_name: str
    subject_name: str | None


class ResolutionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- queries ------------------------------------------------------------

    def list_drifts(
        self,
        *,
        workspace_name: str | None = None,
        status: str = DriftStatus.OPEN.value,
        drift_type: str | None = None,
    ) -> list[DriftListRecord]:
        stmt = (
            select(Drift, Workspace.name, Entity.canonical_name)
            .join(ScanRun, Drift.scan_run_id == ScanRun.id)
            .join(Workspace, ScanRun.workspace_id == Workspace.id)
            .outerjoin(Entity, Drift.subject_entity_id == Entity.id)
        )
        if status != "all":
            stmt = stmt.where(Drift.status == status)
        if drift_type is not None:
            stmt = stmt.where(Drift.type == drift_type)
        if workspace_name is not None:
            stmt = stmt.where(Workspace.name == workspace_name)

        rows = self.session.execute(stmt).all()
        records = [
            DriftListRecord(drift=row[0], workspace_name=row[1], subject_name=row[2])
            for row in rows
        ]
        records.sort(
            key=lambda r: (
                -SEVERITY_RANK[Severity(r.drift.severity)],
                r.drift.detected_at,
                str(r.drift.id),
            )
        )
        return records

    def get_drift(self, drift_id: uuid.UUID) -> Drift:
        drift = self.session.get(Drift, drift_id)
        if drift is None:
            raise UserInputError(f"drift not found: {drift_id}")
        return drift

    # -- mutations ----------------------------------------------------------

    def resolve(
        self,
        drift_id: uuid.UUID,
        *,
        decision: str,
        resolved_by: str,
        reason: str | None = None,
        reason_code: str | None = None,
        authority_fact_id: uuid.UUID | None = None,
    ) -> Resolution:
        drift = self.get_drift(drift_id)
        if drift.status != DriftStatus.OPEN.value:
            raise DomainValidationError(
                f"drift {drift_id} is already {drift.status}; "
                "one resolution per drift in Phase 0"
            )

        try:
            parsed_decision = ResolutionDecision(decision)
        except ValueError as exc:
            raise UserInputError(
                f"invalid decision '{decision}'; expected one of: "
                + ", ".join(d.value for d in ResolutionDecision)
            ) from exc

        parsed_code = None
        if reason_code is not None:
            try:
                parsed_code = ReasonCode(reason_code)
            except ValueError as exc:
                raise UserInputError(
                    f"invalid reason_code '{reason_code}'; expected one of: "
                    + ", ".join(c.value for c in ReasonCode)
                ) from exc

        authority_id = self._resolve_authority(
            drift, parsed_decision, authority_fact_id
        )

        resolution = Resolution(
            drift_id=drift.id,
            resolved_by=resolved_by,
            decision=parsed_decision.value,
            authority_fact_id=authority_id,
            reason_code=parsed_code.value if parsed_code else None,
            reason=reason,
            scope=ResolutionScope.SINGLE.value,
            pattern_jsonb=None,
        )
        self.session.add(resolution)
        drift.status = DriftStatus.RESOLVED.value
        self.session.flush()
        return resolution

    def ignore(
        self,
        drift_id: uuid.UUID,
        *,
        reason: str | None = None,
    ) -> Drift:
        drift = self.get_drift(drift_id)
        if drift.status == DriftStatus.RESOLVED.value:
            raise DomainValidationError(
                f"drift {drift_id} is resolved and cannot be ignored; "
                "resolved findings are permanent in Phase 0"
            )
        if drift.status == DriftStatus.IGNORED.value:
            return drift  # idempotent

        drift.status = DriftStatus.IGNORED.value
        if reason is not None:
            # Reassign (not mutate) so SQLAlchemy detects the JSONB change.
            drift.detail_jsonb = {
                **(drift.detail_jsonb or {}),
                "ignore_reason": reason,
            }
        self.session.flush()
        return drift

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _resolve_authority(
        drift: Drift,
        decision: ResolutionDecision,
        explicit: uuid.UUID | None,
    ) -> uuid.UUID | None:
        if explicit is not None:
            allowed = {
                pid
                for pid in (drift.old_fact_id, drift.new_fact_id)
                if pid is not None
            }
            if not allowed:
                raise DomainValidationError(
                    "an explicit authority_fact_id cannot apply to this drift: "
                    "it references documents, not facts"
                )
            if explicit not in allowed:
                raise DomainValidationError(
                    "authority_fact_id must be one of the drift's old/new facts"
                )
            return explicit

        if decision is ResolutionDecision.ACCEPT_NEWER:
            return drift.new_fact_id
        if decision is ResolutionDecision.KEEP_OLD:
            return drift.old_fact_id
        return None
