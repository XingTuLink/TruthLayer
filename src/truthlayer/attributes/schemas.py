"""Pydantic shapes for the attribute-clustering LLM call (attribute-resolve-v1).

The model performs an open-vocabulary *partition* of one subject's
predicates — no domain enumerations anywhere — but the output itself is a
closed, validated structure. Fields are re-validated against the original
predicate texts and structured anchors before anything is trusted
(``resolver.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Closed value-type axis (design §4). This is NOT a domain vocabulary: it
#: describes *how to compare values*, not what business concept the attribute
#: is.
ValueKind = Literal["measure", "enumeration", "text", "date", "boolean", "entity_ref"]

NonBlank = str


class AliasOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Must exactly match one of the input predicate surface forms.
    predicate: NonBlank
    #: Short verbatim span from the predicate/value text supporting why this
    #: alias belongs to the cluster. Missing/unverifiable spans downgrade the
    #: merge to pending review (§7-3).
    evidence_span: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("predicate")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("predicate must not be blank")
        return stripped

    @field_validator("evidence_span")
    @classmethod
    def _strip_span(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AttributeClusterOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_name: NonBlank
    definition: NonBlank
    value_kind: ValueKind
    aliases: list[AliasOut] = Field(min_length=1)
    #: Optional text-value equivalences the model is confident about for
    #: non-measure attributes (e.g. "需提前审批" ≈ "事前申请"). Each pair is
    #: still treated as a *candidate* (normalized-equivalent channel), never
    #: as blocking evidence.
    equivalent_text_values: list[list[str]] = Field(default_factory=list)

    @field_validator("canonical_name", "definition")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("equivalent_text_values")
    @classmethod
    def _pairs_of_two(cls, value: list[list[str]]) -> list[list[str]]:
        for pair in value:
            if len(pair) != 2 or not all(isinstance(v, str) and v.strip() for v in pair):
                raise ValueError("equivalent_text_values entries must be [a, b] strings")
        return value


class SubjectClustersOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Echoed canonical subject name; sections that do not match an input
    #: subject are discarded.
    subject: NonBlank
    clusters: list[AttributeClusterOut] = Field(default_factory=list)

    @field_validator("subject")
    @classmethod
    def _strip_subject(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("subject must not be blank")
        return stripped


class AttributeResolutionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjects: list[SubjectClustersOut] = Field(min_length=1)
