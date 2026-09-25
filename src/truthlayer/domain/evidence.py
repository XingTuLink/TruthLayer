"""Evidence — the strongest rule in the system (#2, #13).

No Evidence, No Trusted Fact. Anything written to ``facts.evidence_jsonb``
must be validated through this schema.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

NonBlank = Annotated[str, Field(min_length=1)]


class Evidence(BaseModel):
    """A pointer to the exact location supporting a Fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: uuid.UUID
    chunk_id: uuid.UUID
    page: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)
    quote: NonBlank
    source_type: NonBlank
    authority_score: float = Field(default=0.9, ge=0.0, le=1.0)

    @field_validator("quote", "source_type")
    @classmethod
    def _strip_and_require_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped
