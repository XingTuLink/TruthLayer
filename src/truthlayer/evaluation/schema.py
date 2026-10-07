"""Pydantic schema for golden QA cases (#39).

A case is a self-contained knowledge scenario:

* input documents / entities / facts (the world *after* extraction);
* the rule context (``as_of`` + thresholds + multi-valued escape hatch);
* ``expect.drifts`` — the findings a correct detector must produce.

Positive cases expect >= 1 drift; negative and ambiguous-negative cases must
expect exactly zero findings. That invariant is enforced here so a
mistyped case can never silently game the metrics.
"""

from __future__ import annotations

import enum
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from truthlayer.config import DEFAULT_IMMUTABLE_METADATA_PREDICATES
from truthlayer.domain.enums import DriftType, Severity


class CaseCategory(str, enum.Enum):
    POSITIVE = "positive"
    """Real drift — the detector must flag it."""

    NEGATIVE = "negative"
    """Normal knowledge — no finding allowed."""

    AMBIGUOUS_NEGATIVE = "ambiguous_negative"
    """Looks like a conflict, but time / scope / subject differ — no finding."""


ScalarTypeName = Literal["number", "string", "boolean", "date", "entity"]


class QARules(BaseModel):
    """Rule inputs mirroring :class:`DetectionContext` for one case."""

    model_config = ConfigDict(extra="forbid")

    stale_after_days: int = Field(default=365, ge=1)
    pricing_stale_days: int | None = Field(default=None, ge=1)
    multi_valued_predicates: list[str] = Field(default_factory=list)
    #: Defaults to production RulesConfig defaults so Golden cases exercise
    #: out-of-the-box behaviour (immutable edition-metadata suppression).
    immutable_metadata_predicates: list[str] = Field(
        default_factory=lambda: list(DEFAULT_IMMUTABLE_METADATA_PREDICATES)
    )
    #: Override keys like "pricing_change" / "policy_change".
    severity: dict[str, Severity] = Field(default_factory=dict)


class QADocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    source_type: str = "general"
    authority: float = Field(default=0.9, ge=0.0, le=1.0)
    parsed_at: date | None = None
    #: Local id of the document this one *explicitly* supersedes. Ingestion
    #: derives this edge from the version mapping; cases declare it directly.
    previous_version_of: str | None = None
    #: Optional version-chain group; stable across a replacement chain.
    group: str | None = None


class QAEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = "product"
    #: Optional embedding; only used to exercise embedding recall (#23).
    embedding: list[float] | None = None


class QAFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    subject: str = Field(min_length=1, description="local entity id")
    predicate: str = Field(min_length=1)
    #: Scalar value; mutually exclusive with object_entity.
    value: str | int | float | bool | date | None = None
    value_type: ScalarTypeName = "string"
    #: Local entity id when value_type == "entity".
    object_entity: str | None = None
    #: Local document id; None = fact with no source document.
    source: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    observed_at: date | None = None
    #: Structured measure anchor (fact-extract-v5+); R16 matches reused
    #: values only within the same parsed measure dimension.
    measure_unit: str | None = None
    #: valid_to provenance (fact-extract-v6+): quoted / document_scope /
    #: calendar_derived. Omitted on legacy cases and treated as trusted.
    valid_to_anchor: Literal[
        "quoted", "document_scope", "calendar_derived"
    ] | None = None

    @model_validator(mode="after")
    def _validate_object(self) -> "QAFact":
        if self.value_type == "entity":
            if self.object_entity is None:
                raise ValueError("object_entity is required when value_type=entity")
            if self.value is not None:
                raise ValueError("value must be omitted when value_type=entity")
        elif self.value is None:
            raise ValueError(
                f"fact {self.id!r}: value is required for value_type="
                f"{self.value_type}"
            )
        return self


class ExpectedDrift(BaseModel):
    """A finding the detector must produce.

    ``type`` is always required; the remaining fields are optional locators
    that narrow the match (subject / predicate / severity / source files).
    """

    model_config = ConfigDict(extra="forbid")

    type: DriftType
    subject: str | None = Field(default=None, description="entity local id")
    predicate: str | None = None
    severity: Severity | None = None
    old_source: str | None = None
    new_source: str | None = None

    def locator_count(self) -> int:
        return sum(
            v is not None
            for v in (
                self.subject,
                self.predicate,
                self.severity,
                self.old_source,
                self.new_source,
            )
        )


class QACase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: CaseCategory
    #: Fixed evaluation anchor so cases stay deterministic forever.
    as_of: date = date(2026, 6, 1)
    rules: QARules = Field(default_factory=QARules)
    documents: list[QADocument] = Field(default_factory=list)
    entities: list[QAEntity] = Field(default_factory=list)
    facts: list[QAFact] = Field(default_factory=list)
    expected_drifts: list[ExpectedDrift] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_references_and_expectation(self) -> "QACase":
        doc_ids = {d.id for d in self.documents}
        ent_ids = {e.id for e in self.entities}

        for doc in self.documents:
            if doc.previous_version_of is not None:
                if doc.previous_version_of not in doc_ids:
                    raise ValueError(
                        f"case {self.id!r}: document {doc.id!r} "
                        f"previous_version_of unknown doc "
                        f"{doc.previous_version_of!r}"
                    )
                if doc.previous_version_of == doc.id:
                    raise ValueError(
                        f"case {self.id!r}: document {doc.id!r} cannot supersede itself"
                    )

        for fact in self.facts:
            if fact.subject not in ent_ids:
                raise ValueError(
                    f"case {self.id!r}: fact {fact.id!r} references unknown "
                    f"subject entity {fact.subject!r}"
                )
            if fact.object_entity is not None and fact.object_entity not in ent_ids:
                raise ValueError(
                    f"case {self.id!r}: fact {fact.id!r} references unknown "
                    f"object entity {fact.object_entity!r}"
                )
            if fact.source is not None and fact.source not in doc_ids:
                raise ValueError(
                    f"case {self.id!r}: fact {fact.id!r} references unknown "
                    f"source document {fact.source!r}"
                )

        for exp in self.expected_drifts:
            if exp.subject is not None and exp.subject not in ent_ids:
                raise ValueError(
                    f"case {self.id!r}: expected drift references unknown "
                    f"subject {exp.subject!r}"
                )

        # Category/expectation invariant — the backbone of honest metrics.
        if self.category is CaseCategory.POSITIVE:
            if not self.expected_drifts:
                raise ValueError(
                    f"case {self.id!r}: positive case must expect >= 1 drift"
                )
        elif self.expected_drifts:
            raise ValueError(
                f"case {self.id!r}: {self.category.value} case must expect "
                f"zero drifts"
            )
        return self


class QACaseSuite(BaseModel):
    """One YAML file = one suite of cases."""

    model_config = ConfigDict(extra="forbid")

    cases: list[QACase] = Field(min_length=1)
