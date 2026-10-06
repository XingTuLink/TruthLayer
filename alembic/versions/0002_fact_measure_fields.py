"""facts: structured measure anchor columns (unit/currency/tax_basis)

Additive, nullable columns backing fact-extract-v5: measurable numeric facts
store their unit / currency / tax treatment as structured fields instead of
jamming them into predicate text. Pre-v5 rows stay NULL; no CHECK constraints
are added (invariants are enforced at extraction/Pydantic layer), and drift
fingerprints are unchanged.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "facts",
        sa.Column("measure_unit", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "facts",
        sa.Column("currency", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "facts",
        sa.Column("tax_basis", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("facts", "tax_basis")
    op.drop_column("facts", "currency")
    op.drop_column("facts", "measure_unit")
