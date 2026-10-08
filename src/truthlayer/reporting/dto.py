"""Report DTO — the single shape shared by JSON / HTML / future API (#56).

Plain Pydantic v2 models assembled from ORM rows by ``builder.py``; renderers
must not touch the database or the session.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSnippet(DTO):
    document_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    filename: str | None = None
    quote: str
    source_type: str | None = None
    authority_score: float | None = None
    page: int | None = None


class FactSnippet(DTO):
    fact_id: uuid.UUID
    subject: str
    predicate: str
    object_display: str
    object_type: str  # "string" | "number" | "date" | "boolean" | "entity"
    valid_from: date | None = None
    valid_to: date | None = None
    observed_at: datetime | None = None
    status: str
    confidence: float
    evidence: list[EvidenceSnippet] = Field(default_factory=list)


class SourceSnippet(DTO):
    document_id: uuid.UUID
    filename: str
    source_type: str | None = None
    authority_score: float
    version_label: str | None = None


class ResolutionSnippet(DTO):
    decision: str
    resolved_by: str
    resolved_at: datetime
    reason_code: str | None = None
    reason: str | None = None
    authority_fact_id: uuid.UUID | None = None


class IssueReport(DTO):
    """One drift with everything #33 requires a human to decide."""

    id: uuid.UUID
    drift_type: str
    severity: str
    status: str
    detector_type: str
    ai_impact_level: str
    confidence: float | None = None
    detected_at: datetime
    subject: str | None = None
    predicate: str | None = None

    title: str
    human_title: str | None = None
    why: str
    recommendation: str
    suggested_decisions: list[str] = Field(default_factory=list)

    old_fact: FactSnippet | None = None
    new_fact: FactSnippet | None = None
    old_source: SourceSnippet | None = None
    new_source: SourceSnippet | None = None
    evidence: list[EvidenceSnippet] = Field(default_factory=list)

    detail: dict = Field(default_factory=dict)
    new_this_scan: bool = False
    resolution: ResolutionSnippet | None = None


class IssueGroup(DTO):
    """Document-level rollup of fact-level stale findings (R11).

    One superseded handbook produces dozens of ``confirmed_stale`` rows;
    showing them one card per fact floods the report. The group presents the
    real, document-scoped problem while every member issue stays available
    (with its drift id) for drill-down and per-fact resolution.
    """

    group_type: str = "document_stale"
    drift_type: str  # "confirmed_stale" | "possibly_stale"
    severity: str  # highest severity among the member issues
    document_id: uuid.UUID
    filename: str
    source: SourceSnippet | None = None
    count: int
    title: str
    why: str
    recommendation: str
    suggested_decisions: list[str] = Field(default_factory=list)
    first_detected_at: datetime
    issues: list[IssueReport] = Field(default_factory=list)
    #: detail.reason of the head issue; identifies a group headed by a
    #: target=document self-declared-expiry card ("document_self_declared_expired").
    head_reason: str | None = None
    #: Head's verbatim evidence (the document's own validity statement).
    evidence: list[EvidenceSnippet] = Field(default_factory=list)


class CIBadge(DTO):
    fail_on: str
    triggered: bool
    open_count: int
    blocking_count: int
    by_severity: dict[str, int] = Field(default_factory=dict)


class ReviewItem(DTO):
    """One non-blocking attribute-resolution audit item (phase 1)."""

    channel: str
    reason: str
    subject: str
    predicate_a: str
    predicate_b: str
    value_a: str | int | float | bool | None = None
    value_b: str | int | float | bool | None = None
    source_a: str | None = None
    source_b: str | None = None
    canonical_name: str | None = None
    value_kind: str | None = None


class ReviewChannels(DTO):
    """Three audit channels. These NEVER participate in ci.fail_on.

    ``non_deterministic`` is always True on purpose: items can fluctuate
    across scans because they depend on the model partition (the blocking
    drift channel remains fully reproducible).
    """

    non_deterministic: bool = True
    attribute_prompt_version: str | None = None
    pending_review: list[ReviewItem] = Field(default_factory=list)
    cross_attribute_review: list[ReviewItem] = Field(default_factory=list)
    normalized_equivalent: list[ReviewItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ReportSummary(DTO):
    workspace_id: uuid.UUID
    workspace_name: str
    generated_at: datetime
    tool_version: str
    detector_version: str
    scan_run_id: uuid.UUID | None = None
    knowledge_hash: str | None = None

    documents_total: int = 0
    entities_total: int = 0
    facts_total: int = 0

    new_this_scan: int = 0
    suppressed_this_scan: int = 0

    open_total: int = 0
    ignored_total: int = 0
    resolved_total: int = 0
    open_by_type: dict[str, int] = Field(default_factory=dict)
    #: Document-level stale rollups (R11); purely presentational, CI counts
    #: above stay at fact granularity.
    open_groups_total: int = 0

    ci: CIBadge

    #: Present only for scans that ran the attribute-resolution stage.
    attribute_review: ReviewChannels | None = None


class ReportDTO(DTO):
    summary: ReportSummary
    issues: list[IssueReport] = Field(default_factory=list)
    groups: list[IssueGroup] = Field(default_factory=list)
