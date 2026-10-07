"""Assemble ReportDTO / IssueReport from ORM rows.

This is the ONLY place that knows how drift rows, facts, documents and
evidence join together. Renderers (JSON/HTML) and the CLI ``drift show``
command consume the assembled DTO without touching the session.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from truthlayer import __version__
from truthlayer.config import TruthLayerConfig
from truthlayer.db.orm import (
    Document,
    Drift,
    Entity,
    Fact,
    Resolution,
    ScanRun,
    Snapshot,
    Workspace,
)
from truthlayer.detection.service import DETECTOR_VERSION, DetectionResult
from truthlayer.domain.enums import SEVERITY_RANK, DriftStatus
from truthlayer.reporting.dto import (
    CIBadge,
    EvidenceSnippet,
    FactSnippet,
    IssueReport,
    ReportDTO,
    ReportSummary,
    ResolutionSnippet,
    ReviewChannels,
    ReviewItem,
    SourceSnippet,
)
from truthlayer.reporting.humanize import narrative, plain_title
from truthlayer.reporting.policy import count_blocking


def _format_scalar(value: object, object_type: str | None) -> str:
    if object_type == "boolean":
        return "是" if value is True else "否"
    if value is None:
        return ""
    return str(value)


def _review_scalar(value: object) -> str | int | float | bool | None:
    """Coerce channel values (dates restored by KnowledgeState, etc.)."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class ReportBuilder:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- public API ---------------------------------------------------------

    def build(
        self,
        workspace_id: uuid.UUID,
        config: TruthLayerConfig,
        *,
        scan_run: ScanRun | None = None,
        detection: DetectionResult | None = None,
    ) -> ReportDTO:
        workspace = self.session.get(Workspace, workspace_id)
        new_ids = {
            item.id
            for item in (detection.items if detection else ())
            if item.is_new and item.id is not None
        }

        drifts = list(
            self.session.scalars(
                select(Drift)
                .join(ScanRun, Drift.scan_run_id == ScanRun.id)
                .where(ScanRun.workspace_id == workspace_id)
            )
        )
        status_counts = Counter(d.status for d in drifts)
        open_drifts = [d for d in drifts if d.status == DriftStatus.OPEN.value]

        context = self._load_context(open_drifts)
        resolutions = self._load_resolutions([d.id for d in open_drifts])

        issues = [
            self._build_issue(
                drift,
                context,
                resolution=resolutions.get(drift.id),
                new_this_scan=drift.id in new_ids,
            )
            for drift in open_drifts
        ]
        issues.sort(
            key=lambda i: (
                -SEVERITY_RANK[i.severity],
                i.detected_at,
                str(i.id),
            )
        )

        open_severities = Counter(d.severity for d in open_drifts)
        fail_on = config.ci.fail_on
        blocking = count_blocking(open_severities, fail_on)
        ci = CIBadge(
            fail_on=fail_on.value,
            triggered=blocking > 0,
            open_count=len(open_drifts),
            blocking_count=blocking,
            by_severity={s.value: open_severities.get(s.value, 0)
                         for s in SEVERITY_RANK},
        )

        knowledge_hash = self._latest_hash(workspace_id, scan_run)
        summary = ReportSummary(
            workspace_id=workspace_id,
            workspace_name=workspace.name if workspace else str(workspace_id),
            generated_at=datetime.now(timezone.utc),
            tool_version=__version__,
            detector_version=DETECTOR_VERSION,
            scan_run_id=scan_run.id if scan_run else None,
            knowledge_hash=knowledge_hash,
            documents_total=self._count(
                Document, Document.workspace_id == workspace_id
            ),
            entities_total=self._count(
                Entity, Entity.workspace_id == workspace_id
            ),
            facts_total=self._count(
                Fact,
                Fact.workspace_id == workspace_id,
                Fact.status == "active",
            ),
            new_this_scan=detection.new_count if detection else 0,
            suppressed_this_scan=detection.suppressed_count if detection else 0,
            open_total=status_counts.get(DriftStatus.OPEN.value, 0),
            ignored_total=status_counts.get(DriftStatus.IGNORED.value, 0),
            resolved_total=status_counts.get(DriftStatus.RESOLVED.value, 0),
            open_by_type={
                drift_type: sum(
                    1 for d in open_drifts if d.type == drift_type
                )
                for drift_type in (
                    "conflict",
                    "possibly_stale",
                    "confirmed_stale",
                    "reused_stale_value",
                    "superseded",
                    "duplicate",
                )
            },
            ci=ci,
            attribute_review=self._review_channels(detection),
        )
        return ReportDTO(summary=summary, issues=issues)

    @staticmethod
    def _review_channels(
        detection: DetectionResult | None,
    ) -> ReviewChannels | None:
        if detection is None or detection.attribute_prompt_version is None:
            return None

        def to_item(channel: str, item) -> ReviewItem:
            return ReviewItem(
                channel=channel,
                reason=item.reason,
                subject=item.subject_name,
                predicate_a=item.predicate_a,
                predicate_b=item.predicate_b,
                value_a=_review_scalar(item.value_a),
                value_b=_review_scalar(item.value_b),
                source_a=item.source_a,
                source_b=item.source_b,
                canonical_name=item.canonical_name,
                value_kind=item.value_kind,
            )

        return ReviewChannels(
            attribute_prompt_version=detection.attribute_prompt_version,
            pending_review=[
                to_item("pending_review", item)
                for item in detection.pending_review
            ],
            cross_attribute_review=[
                to_item("cross_attribute_review", item)
                for item in detection.cross_attribute_review
            ],
            normalized_equivalent=[
                to_item("normalized_equivalent", item)
                for item in detection.normalized_equivalent
            ],
            notes=list(detection.attribute_notes),
        )

    def build_issue(self, drift: Drift) -> IssueReport:
        """Full detail for one drift of ANY status (used by ``drift show``)."""
        context = self._load_context([drift])
        resolution = self._load_resolutions([drift.id]).get(drift.id)
        return self._build_issue(drift, context, resolution=resolution)

    # -- internals ----------------------------------------------------------

    def _count(self, model: type, *conditions) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(model).where(*conditions)
            )
            or 0
        )

    def _latest_hash(
        self,
        workspace_id: uuid.UUID,
        scan_run: ScanRun | None,
    ) -> str | None:
        if scan_run is not None:
            snapshot = self.session.scalar(
                select(Snapshot).where(Snapshot.scan_run_id == scan_run.id)
            )
            if snapshot is not None:
                return snapshot.knowledge_hash
        snapshot = self.session.scalar(
            select(Snapshot)
            .where(Snapshot.workspace_id == workspace_id)
            .order_by(Snapshot.created_at.desc())
        )
        return snapshot.knowledge_hash if snapshot else None

    def _load_context(self, drifts: list[Drift]) -> dict:
        fact_ids: set[uuid.UUID] = set()
        doc_ids: set[uuid.UUID] = set()
        entity_ids: set[uuid.UUID] = set()
        for d in drifts:
            if d.old_fact_id:
                fact_ids.add(d.old_fact_id)
            if d.new_fact_id:
                fact_ids.add(d.new_fact_id)
            if d.old_document_id:
                doc_ids.add(d.old_document_id)
            if d.new_document_id:
                doc_ids.add(d.new_document_id)
            if d.subject_entity_id:
                entity_ids.add(d.subject_entity_id)

        facts = {
            f.id: f
            for f in self.session.scalars(
                select(Fact).where(Fact.id.in_(fact_ids or {uuid.UUID(int=0)}))
            )
        }
        # Evidence points at documents/chunks we would not otherwise load.
        for fact in facts.values():
            entity_ids.add(fact.subject_entity_id)
            if fact.object_entity_id:
                entity_ids.add(fact.object_entity_id)
            for ev in fact.evidence_jsonb or []:
                doc_id = ev.get("document_id")
                if doc_id:
                    doc_ids.add(uuid.UUID(str(doc_id)))

        documents = {
            d.id: d
            for d in self.session.scalars(
                select(Document).where(
                    Document.id.in_(doc_ids or {uuid.UUID(int=0)})
                )
            )
        }
        entities = {
            e.id: e
            for e in self.session.scalars(
                select(Entity).where(
                    Entity.id.in_(entity_ids or {uuid.UUID(int=0)})
                )
            )
        }
        return {"facts": facts, "documents": documents, "entities": entities}

    def _load_resolutions(
        self, drift_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Resolution]:
        if not drift_ids:
            return {}
        rows = self.session.scalars(
            select(Resolution).where(
                Resolution.drift_id.in_(drift_ids)
            )
        )
        return {r.drift_id: r for r in rows}

    def _build_issue(
        self,
        drift: Drift,
        context: dict,
        *,
        resolution: Resolution | None = None,
        new_this_scan: bool = False,
    ) -> IssueReport:
        facts: dict[uuid.UUID, Fact] = context["facts"]
        documents: dict[uuid.UUID, Document] = context["documents"]
        entities: dict[uuid.UUID, Entity] = context["entities"]
        detail = dict(drift.detail_jsonb or {})

        subject = None
        if drift.subject_entity_id and drift.subject_entity_id in entities:
            subject = entities[drift.subject_entity_id].canonical_name
        else:
            subject = detail.get("subject")

        old_fact = self._fact_snippet(
            facts.get(drift.old_fact_id), context
        ) if drift.old_fact_id else None
        new_fact = self._fact_snippet(
            facts.get(drift.new_fact_id), context
        ) if drift.new_fact_id else None
        old_source = self._source_snippet(
            documents.get(drift.old_document_id)
        ) if drift.old_document_id else None
        new_source = self._source_snippet(
            documents.get(drift.new_document_id)
        ) if drift.new_document_id else None

        evidence = self._collect_evidence(old_fact, new_fact)
        story = narrative(drift.type, detail)

        return IssueReport(
            id=drift.id,
            drift_type=drift.type,
            severity=drift.severity,
            status=drift.status,
            detector_type=drift.detector_type,
            ai_impact_level=drift.ai_impact_level,
            confidence=drift.confidence,
            detected_at=drift.detected_at,
            subject=subject,
            predicate=drift.predicate,
            title=story.title,
            human_title=plain_title(drift.type, detail),
            why=story.why,
            recommendation=story.recommendation,
            suggested_decisions=list(story.suggested_decisions),
            old_fact=old_fact,
            new_fact=new_fact,
            old_source=old_source,
            new_source=new_source,
            evidence=evidence,
            detail={k: v for k, v in detail.items() if k != "fingerprint"},
            new_this_scan=new_this_scan,
            resolution=self._resolution_snippet(resolution),
        )

    def _fact_snippet(
        self,
        fact: Fact | None,
        context: dict,
    ) -> FactSnippet | None:
        if fact is None:
            # FK is ON DELETE SET NULL: a dangling pointer must not crash
            # report generation; the drift record itself stays intact.
            return None
        entities = context["entities"]
        if fact.object_entity_id is not None:
            target = entities.get(fact.object_entity_id)
            object_display = target.canonical_name if target else "(已删除实体)"
            object_type = "entity"
        else:
            object_display = _format_scalar(fact.object_value, fact.object_type)
            object_type = fact.object_type or "string"

        subject = entities.get(fact.subject_entity_id)
        documents = context["documents"]
        evidence: list[EvidenceSnippet] = []
        for item in fact.evidence_jsonb or []:
            doc_id = item.get("document_id")
            doc = documents.get(uuid.UUID(str(doc_id))) if doc_id else None
            evidence.append(
                EvidenceSnippet(
                    document_id=uuid.UUID(str(doc_id)) if doc_id else None,
                    chunk_id=uuid.UUID(str(item["chunk_id"]))
                    if item.get("chunk_id")
                    else None,
                    filename=doc.filename if doc else None,
                    quote=item["quote"],
                    source_type=item.get("source_type"),
                    authority_score=item.get("authority_score"),
                    page=item.get("page"),
                )
            )

        return FactSnippet(
            fact_id=fact.id,
            subject=subject.canonical_name if subject else "(未知主体)",
            predicate=fact.predicate,
            object_display=object_display,
            object_type=object_type,
            valid_from=fact.valid_from,
            valid_to=fact.valid_to,
            observed_at=fact.observed_at,
            status=fact.status,
            confidence=fact.confidence,
            evidence=evidence,
        )

    @staticmethod
    def _source_snippet(document: Document | None) -> SourceSnippet | None:
        if document is None:
            return None
        meta = document.metadata_jsonb or {}
        return SourceSnippet(
            document_id=document.id,
            filename=document.filename,
            source_type=meta.get("source_type"),
            authority_score=document.authority_score,
            version_label=document.version_label,
        )

    @staticmethod
    def _collect_evidence(
        old_fact: FactSnippet | None,
        new_fact: FactSnippet | None,
    ) -> list[EvidenceSnippet]:
        seen: set[tuple] = set()
        out: list[EvidenceSnippet] = []
        for snippet in (
            *(old_fact.evidence if old_fact else ()),
            *(new_fact.evidence if new_fact else ()),
        ):
            key = (str(snippet.chunk_id), snippet.quote)
            if key in seen:
                continue
            seen.add(key)
            out.append(snippet)
        return out

    @staticmethod
    def _resolution_snippet(
        resolution: Resolution | None,
    ) -> ResolutionSnippet | None:
        if resolution is None:
            return None
        return ResolutionSnippet(
            decision=resolution.decision,
            resolved_by=resolution.resolved_by,
            resolved_at=resolution.resolved_at,
            reason_code=resolution.reason_code,
            reason=resolution.reason,
            authority_fact_id=resolution.authority_fact_id,
        )
