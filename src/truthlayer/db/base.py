"""SQLAlchemy declarative base and shared DB constants."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Embedding columns are deliberately dimension-agnostic: different providers
# use different dimensionality (1536 / 1024 / 768 ...) and one workspace may
# switch providers. ANN indexes (Sprint 4) pin the dimension at index creation
# time; the dimension actually in use is recorded per scan_run (#23).

# Deterministic constraint names: reproducible schemas/hashes matter (#5, #30).
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
