"""LLM-facing extraction schemas.

These are *candidate* shapes: the model's output is validated here, then
re-validated against the hard domain rules (FactClaim XOR / time window,
Evidence quote / authority) before anything becomes a trusted Fact (#22).
Dates cross the wire as ISO strings (YYYY-MM-DD) and are parsed by the
service, keeping this layer free of database types.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonBlank = Annotated[str, Field(min_length=1)]
IsoDate = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="ISO date YYYY-MM-DD",
    ),
]
ScalarObjectType = Literal["string", "number", "date", "boolean"]


class RawEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonBlank
    type: NonBlank
    aliases: list[NonBlank] = Field(default_factory=list)

    @field_validator("name", "type")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("aliases")
    @classmethod
    def _strip_aliases(cls, values: list[str]) -> list[str]:
        return [v.strip() for v in values if v.strip()]


class RawFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: NonBlank
    predicate: NonBlank
    object_entity: NonBlank | None = None
    object_value: str | int | float | bool | None = None
    object_type: ScalarObjectType | None = None
    valid_from: IsoDate | None = None
    valid_to: IsoDate | None = None
    observed_at: IsoDate | None = None
    quote: NonBlank
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("subject", "predicate", "object_entity", "quote")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def _xor_object(self) -> "RawFact":
        has_entity = self.object_entity is not None
        has_value = self.object_value is not None
        if has_entity == has_value:
            raise ValueError(
                "exactly one of object_entity / object_value must be set"
            )
        if has_entity and self.object_type is not None:
            raise ValueError(
                "object_type must be null when object_entity is set"
            )
        if has_value and self.object_type is None:
            raise ValueError(
                "object_type is required when object_value is a scalar"
            )
        return self


class ExtractionEnvelope(BaseModel):
    """One envelope per chunk. Empty lists mean "nothing extracted"."""

    model_config = ConfigDict(extra="forbid")

    entities: list[RawEntity] = Field(default_factory=list)
    facts: list[RawFact] = Field(default_factory=list)
