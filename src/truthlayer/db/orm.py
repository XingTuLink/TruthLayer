"""SQLAlchemy 2.x ORM models — the 13 core domain tables (#6).

Enums are stored as plain String columns to keep migrations cheap; value
validity is guaranteed by the domain layer. The polymorphic Drift.target_id
is intentionally a plain UUID with no database FK (#17).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from truthlayer.db.base import Base

_JSONB_OBJ = "('{}'::jsonb)"
_JSONB_ARR = "('[]'::jsonb)"


class _CreatedAt:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class _Timestamps(_CreatedAt):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --- Knowledge containers ----------------------------------------------------

class Workspace(_Timestamps, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("name", name="uq_workspaces_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class DocumentGroup(_Timestamps, Base):
    """A logical document; its rows form a version chain (#7, #8)."""

    __tablename__ = "document_groups"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_document_groups_workspace_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_document_groups_workspace_id_workspaces",
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class Document(_Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "file_hash",
            name="uq_documents_workspace_file_hash",
        ),
        CheckConstraint(
            "authority_score >= 0.0 AND authority_score <= 1.0",
            name="authority_score",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_documents_workspace_id_workspaces",
        ),
        nullable=False,
    )
    document_group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "document_groups.id",
            ondelete="SET NULL",
            name="fk_documents_document_group_id_document_groups",
        ),
        nullable=True,
    )
    version_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="SET NULL",
            name="fk_documents_previous_version_id_documents",
        ),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), server_default="pending", nullable=False
    )
    authority_score: Mapped[float] = mapped_column(
        Float, server_default=sa_text("0.9"), nullable=False
    )
    metadata_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sa_text(_JSONB_OBJ), nullable=False
    )


class Chunk(_Timestamps, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_chunks_document_chunk_index",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
            name="fk_chunks_document_id_documents",
        ),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(
        Integer, server_default=sa_text("0"), nullable=False
    )
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(), nullable=True
    )
    metadata_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sa_text(_JSONB_OBJ), nullable=False
    )


# --- Extracted knowledge -----------------------------------------------------

class Entity(_Timestamps, Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "canonical_name",
            "entity_type",
            name="uq_entities_workspace_name_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_entities_workspace_id_workspaces",
        ),
        nullable=False,
    )
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(), nullable=True
    )
    metadata_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sa_text(_JSONB_OBJ), nullable=False
    )


class EntityAlias(_CreatedAt, Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "alias_text",
            name="uq_entity_aliases_entity_alias",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "entities.id",
            ondelete="CASCADE",
            name="fk_entity_aliases_entity_id_entities",
        ),
        nullable=False,
    )
    alias_text: Mapped[str] = mapped_column(String(512), nullable=False)


class Fact(_Timestamps, Base):
    __tablename__ = "facts"
    __table_args__ = (
        CheckConstraint(
            "((CASE WHEN object_entity_id IS NULL THEN 0 ELSE 1 END) + "
            "(CASE WHEN object_value IS NULL THEN 0 ELSE 1 END)) = 1",
            name="object_xor",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name="valid_window",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="confidence_range",
        ),
        Index(
            "ix_facts_workspace_subject_predicate",
            "workspace_id",
            "subject_entity_id",
            "predicate",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_facts_workspace_id_workspaces",
        ),
        nullable=False,
    )
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "entities.id",
            ondelete="RESTRICT",
            name="fk_facts_subject_entity_id_entities",
        ),
        nullable=False,
    )
    predicate: Mapped[str] = mapped_column(String(255), nullable=False)
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "entities.id",
            ondelete="SET NULL",
            name="fk_facts_object_entity_id_entities",
        ),
        nullable=True,
    )
    # none_as_null: Python None must bind to SQL NULL, otherwise the XOR
    # check sees a JSON 'null' literal as a present scalar value.
    object_value: Mapped[Any | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    object_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confidence: Mapped[float] = mapped_column(
        Float, server_default=sa_text("0.0"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), server_default="active", nullable=False
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "chunks.id",
            ondelete="SET NULL",
            name="fk_facts_source_chunk_id_chunks",
        ),
        nullable=True,
    )
    evidence_jsonb: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=sa_text(_JSONB_ARR), nullable=False
    )


# --- Scan / drift / resolution ----------------------------------------------

class ScanRun(_CreatedAt, Base):
    """One (reproducible) scan execution (#15)."""

    __tablename__ = "scan_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_scan_runs_workspace_id_workspaces",
        ),
        nullable=False,
    )
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), server_default="pending", nullable=False
    )
    document_count: Mapped[int] = mapped_column(
        Integer, server_default=sa_text("0"), nullable=False
    )
    fact_count: Mapped[int] = mapped_column(
        Integer, server_default=sa_text("0"), nullable=False
    )
    drift_count: Mapped[int] = mapped_column(
        Integer, server_default=sa_text("0"), nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class Drift(_CreatedAt, Base):
    __tablename__ = "drifts"
    __table_args__ = (
        Index("ix_drifts_scan_run_id", "scan_run_id"),
        Index("ix_drifts_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scan_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "scan_runs.id",
            ondelete="CASCADE",
            name="fk_drifts_scan_run_id_scan_runs",
        ),
        nullable=False,
    )
    # Polymorphic reference — no DB FK by design (#17).
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), server_default="open", nullable=False
    )

    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "entities.id",
            ondelete="SET NULL",
            name="fk_drifts_subject_entity_id_entities",
        ),
        nullable=True,
    )
    predicate: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "facts.id",
            ondelete="SET NULL",
            name="fk_drifts_old_fact_id_facts",
        ),
        nullable=True,
    )
    new_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "facts.id",
            ondelete="SET NULL",
            name="fk_drifts_new_fact_id_facts",
        ),
        nullable=True,
    )
    old_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="SET NULL",
            name="fk_drifts_old_document_id_documents",
        ),
        nullable=True,
    )
    new_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="SET NULL",
            name="fk_drifts_new_document_id_documents",
        ),
        nullable=True,
    )

    detector_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ai_impact_level: Mapped[str] = mapped_column(
        String(16), server_default="unknown", nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sa_text(_JSONB_OBJ), nullable=False
    )


class Resolution(_CreatedAt, Base):
    __tablename__ = "resolutions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    drift_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "drifts.id",
            ondelete="CASCADE",
            name="fk_resolutions_drift_id_drifts",
        ),
        unique=True,
        nullable=False,
    )
    resolved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    authority_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "facts.id",
            ondelete="SET NULL",
            name="fk_resolutions_authority_fact_id_facts",
        ),
        nullable=True,
    )
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(
        String(16), server_default="single", nullable=False
    )
    pattern_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )


# --- Immutable snapshots (#29) -----------------------------------------------

class Snapshot(_CreatedAt, Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_snapshots_workspace_id_workspaces",
        ),
        nullable=False,
    )
    scan_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "scan_runs.id",
            ondelete="SET NULL",
            name="fk_snapshots_scan_run_id_scan_runs",
        ),
        nullable=True,
    )
    knowledge_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    fact_count: Mapped[int] = mapped_column(
        Integer, server_default=sa_text("0"), nullable=False
    )
    entity_count: Mapped[int] = mapped_column(
        Integer, server_default=sa_text("0"), nullable=False
    )


class SnapshotFact(Base):
    """Append-only snapshot membership (composite PK, #36)."""

    __tablename__ = "snapshot_facts"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "snapshots.id",
            ondelete="CASCADE",
            name="fk_snapshot_facts_snapshot_id_snapshots",
        ),
        primary_key=True,
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "facts.id",
            ondelete="CASCADE",
            name="fk_snapshot_facts_fact_id_facts",
        ),
        primary_key=True,
    )


class SnapshotEntity(Base):
    """Append-only snapshot membership (composite PK, #36)."""

    __tablename__ = "snapshot_entities"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "snapshots.id",
            ondelete="CASCADE",
            name="fk_snapshot_entities_snapshot_id_snapshots",
        ),
        primary_key=True,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "entities.id",
            ondelete="CASCADE",
            name="fk_snapshot_entities_entity_id_entities",
        ),
        primary_key=True,
    )
