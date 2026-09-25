"""initial schema: 13 core domain tables

Revision ID: 0001
Revises:
Create Date: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("name", name="uq_workspaces_name"),
    )

    op.create_table(
        "document_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_document_groups_workspace_id_workspaces",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_groups"),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_document_groups_workspace_name",
        ),
    )

    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(length=512), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column(
            "metadata_jsonb",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("('{}'::jsonb)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_entities_workspace_id_workspaces",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_entities"),
        sa.UniqueConstraint(
            "workspace_id",
            "canonical_name",
            "entity_type",
            name="uq_entities_workspace_name_type",
        ),
    )

    op.create_table(
        "scan_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("config_hash", sa.String(length=128), nullable=False),
        sa.Column("extraction_provider", sa.String(length=64), nullable=True),
        sa.Column("extraction_model", sa.String(length=255), nullable=True),
        sa.Column("embedding_provider", sa.String(length=64), nullable=True),
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("detector_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column(
            "document_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "fact_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "drift_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_scan_runs_workspace_id_workspaces",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan_runs"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_group_id", sa.Uuid(), nullable=True),
        sa.Column("version_label", sa.String(length=255), nullable=True),
        sa.Column("previous_version_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(length=1024), nullable=False),
        sa.Column("file_hash", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column(
            "authority_score",
            sa.Float(),
            server_default=sa.text("0.9"),
            nullable=False,
        ),
        sa.Column(
            "metadata_jsonb",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("('{}'::jsonb)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "authority_score >= 0.0 AND authority_score <= 1.0",
            name="ck_documents_authority_score",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_documents_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["document_group_id"],
            ["document_groups.id"],
            ondelete="SET NULL",
            name="fk_documents_document_group_id_document_groups",
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id"],
            ["documents.id"],
            ondelete="SET NULL",
            name="fk_documents_previous_version_id_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint(
            "workspace_id",
            "file_hash",
            name="uq_documents_workspace_file_hash",
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "token_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column(
            "metadata_jsonb",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("('{}'::jsonb)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="fk_chunks_document_id_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_chunks_document_chunk_index",
        ),
    )

    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("alias_text", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            ondelete="CASCADE",
            name="fk_entity_aliases_entity_id_entities",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_entity_aliases"),
        sa.UniqueConstraint(
            "entity_id",
            "alias_text",
            name="uq_entity_aliases_entity_alias",
        ),
    )

    op.create_table(
        "facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("subject_entity_id", sa.Uuid(), nullable=False),
        sa.Column("predicate", sa.String(length=255), nullable=False),
        sa.Column("object_entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "object_value",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("object_type", sa.String(length=16), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confidence",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=32), server_default="active", nullable=False
        ),
        sa.Column("source_chunk_id", sa.Uuid(), nullable=True),
        sa.Column(
            "evidence_jsonb",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("('[]'::jsonb)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "((CASE WHEN object_entity_id IS NULL THEN 0 ELSE 1 END) + "
            "(CASE WHEN object_value IS NULL THEN 0 ELSE 1 END)) = 1",
            name="ck_facts_object_xor",
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name="ck_facts_valid_window",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_facts_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_facts_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["subject_entity_id"],
            ["entities.id"],
            ondelete="RESTRICT",
            name="fk_facts_subject_entity_id_entities",
        ),
        sa.ForeignKeyConstraint(
            ["object_entity_id"],
            ["entities.id"],
            ondelete="SET NULL",
            name="fk_facts_object_entity_id_entities",
        ),
        sa.ForeignKeyConstraint(
            ["source_chunk_id"],
            ["chunks.id"],
            ondelete="SET NULL",
            name="fk_facts_source_chunk_id_chunks",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_facts"),
    )
    op.create_index(
        "ix_facts_workspace_subject_predicate",
        "facts",
        ["workspace_id", "subject_entity_id", "predicate"],
    )

    op.create_table(
        "drifts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scan_run_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="open", nullable=False
        ),
        sa.Column("subject_entity_id", sa.Uuid(), nullable=True),
        sa.Column("predicate", sa.String(length=255), nullable=True),
        sa.Column("old_fact_id", sa.Uuid(), nullable=True),
        sa.Column("new_fact_id", sa.Uuid(), nullable=True),
        sa.Column("old_document_id", sa.Uuid(), nullable=True),
        sa.Column("new_document_id", sa.Uuid(), nullable=True),
        sa.Column("detector_type", sa.String(length=64), nullable=False),
        sa.Column(
            "ai_impact_level",
            sa.String(length=16),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "detail_jsonb",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("('{}'::jsonb)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scan_run_id"],
            ["scan_runs.id"],
            ondelete="CASCADE",
            name="fk_drifts_scan_run_id_scan_runs",
        ),
        sa.ForeignKeyConstraint(
            ["subject_entity_id"],
            ["entities.id"],
            ondelete="SET NULL",
            name="fk_drifts_subject_entity_id_entities",
        ),
        sa.ForeignKeyConstraint(
            ["old_fact_id"],
            ["facts.id"],
            ondelete="SET NULL",
            name="fk_drifts_old_fact_id_facts",
        ),
        sa.ForeignKeyConstraint(
            ["new_fact_id"],
            ["facts.id"],
            ondelete="SET NULL",
            name="fk_drifts_new_fact_id_facts",
        ),
        sa.ForeignKeyConstraint(
            ["old_document_id"],
            ["documents.id"],
            ondelete="SET NULL",
            name="fk_drifts_old_document_id_documents",
        ),
        sa.ForeignKeyConstraint(
            ["new_document_id"],
            ["documents.id"],
            ondelete="SET NULL",
            name="fk_drifts_new_document_id_documents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_drifts"),
    )
    op.create_index("ix_drifts_scan_run_id", "drifts", ["scan_run_id"])
    op.create_index(
        "ix_drifts_target", "drifts", ["target_type", "target_id"]
    )

    op.create_table(
        "resolutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("drift_id", sa.Uuid(), nullable=False),
        sa.Column("resolved_by", sa.String(length=255), nullable=False),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("authority_fact_id", sa.Uuid(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "scope", sa.String(length=16), server_default="single", nullable=False
        ),
        sa.Column(
            "pattern_jsonb",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["drift_id"],
            ["drifts.id"],
            ondelete="CASCADE",
            name="fk_resolutions_drift_id_drifts",
        ),
        sa.ForeignKeyConstraint(
            ["authority_fact_id"],
            ["facts.id"],
            ondelete="SET NULL",
            name="fk_resolutions_authority_fact_id_facts",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resolutions"),
        sa.UniqueConstraint("drift_id", name="uq_resolutions_drift_id"),
    )

    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("scan_run_id", sa.Uuid(), nullable=True),
        sa.Column("knowledge_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "fact_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "entity_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
            name="fk_snapshots_workspace_id_workspaces",
        ),
        sa.ForeignKeyConstraint(
            ["scan_run_id"],
            ["scan_runs.id"],
            ondelete="SET NULL",
            name="fk_snapshots_scan_run_id_scan_runs",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_snapshots"),
    )

    op.create_table(
        "snapshot_facts",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            ondelete="CASCADE",
            name="fk_snapshot_facts_snapshot_id_snapshots",
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["facts.id"],
            ondelete="CASCADE",
            name="fk_snapshot_facts_fact_id_facts",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "fact_id", name="pk_snapshot_facts"
        ),
    )

    op.create_table(
        "snapshot_entities",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            ondelete="CASCADE",
            name="fk_snapshot_entities_snapshot_id_snapshots",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            ondelete="CASCADE",
            name="fk_snapshot_entities_entity_id_entities",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "entity_id", name="pk_snapshot_entities"
        ),
    )


def downgrade() -> None:
    op.drop_table("snapshot_entities")
    op.drop_table("snapshot_facts")
    op.drop_table("snapshots")
    op.drop_table("resolutions")
    op.drop_index("ix_drifts_target", table_name="drifts")
    op.drop_index("ix_drifts_scan_run_id", table_name="drifts")
    op.drop_table("drifts")
    op.drop_index(
        "ix_facts_workspace_subject_predicate", table_name="facts"
    )
    op.drop_table("facts")
    op.drop_table("entity_aliases")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("scan_runs")
    op.drop_table("entities")
    op.drop_table("document_groups")
    op.drop_table("workspaces")
