"""facts: valid_to evidence-anchor source

Additive, nullable column backing fact-extract-v6: each extracted
``valid_to`` records how it is anchored to evidence — ``quoted`` (verbatim
inside the fact quote), ``document_scope`` (verbatim elsewhere in the same
chunk, e.g. a document header) or ``calendar_derived`` (inferred from a
period label, the date appears nowhere in the source text). Pre-v6 rows
stay NULL and are treated as trusted by the detector (legacy behavior).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "facts",
        sa.Column("valid_to_anchor_source", sa.String(length=24), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("facts", "valid_to_anchor_source")
