"""Fact domain rules (#10, #11).

A Fact has exactly one object: either an entity reference
(``object_entity_id``) or a scalar value (``object_value``), never both and
never neither. Temporal validity must be ordered ``valid_from <= valid_to``.
The same rules are enforced again by database CHECK constraints (#36).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from truthlayer.domain.enums import ObjectType

_SCALAR_TYPES: dict[ObjectType, tuple[type, ...]] = {
    ObjectType.STRING: (str,),
    # bool is a subclass of int — exclude it from NUMBER explicitly.
    ObjectType.NUMBER: (int, float),
    ObjectType.DATE: (date,),
    ObjectType.BOOLEAN: (bool,),
}


class FactClaim(BaseModel):
    """The claim portion of a Fact: subject-predicate-object + validity window.

    This is the pure-domain shape used by extraction and detectors; it knows
    nothing about SQLAlchemy or providers (#53).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_entity_id: uuid.UUID
    predicate: str
    object_entity_id: uuid.UUID | None = None
    object_value: str | int | float | bool | date | None = None
    object_type: ObjectType | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    observed_at: date | None = None

    @model_validator(mode="after")
    def _validate_object_and_window(self) -> "FactClaim":
        has_entity = self.object_entity_id is not None
        has_value = self.object_value is not None

        # XOR constraint (#10).
        if has_entity == has_value:
            raise ValueError(
                "exactly one of object_entity_id / object_value must be set"
            )

        if has_entity:
            if self.object_type is not None:
                raise ValueError(
                    "object_type must be None when object_entity_id is set"
                )
        else:
            self._validate_scalar_value(self.object_value, self.object_type)

        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from > self.valid_to
        ):
            raise ValueError("valid_from must be <= valid_to")

        return self

    @staticmethod
    def _validate_scalar_value(value: Any, object_type: ObjectType | None) -> None:
        if object_type is None:
            raise ValueError(
                "object_type is required when object_value is a scalar"
            )
        expected = _SCALAR_TYPES[object_type]
        if object_type is ObjectType.NUMBER and isinstance(value, bool):
            raise ValueError("boolean value is not a valid number")
        if not isinstance(value, expected):
            raise ValueError(
                f"object_value {value!r} does not match object_type "
                f"{object_type.value}"
            )
