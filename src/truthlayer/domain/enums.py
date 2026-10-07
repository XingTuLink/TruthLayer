"""Domain enumerations.

Values are part of the stable schema (stored as plain strings in the DB);
do not rename existing values without a new migration (development brief #6, #37).
"""

from __future__ import annotations

import enum


class StrEnum(str, enum.Enum):
    """String enum whose ``str()`` is the raw value (JSON/YAML friendly)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# --- Document ----------------------------------------------------------------

class DocumentStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"


# --- Fact (#10, #11) ---------------------------------------------------------

class ObjectType(StrEnum):
    """Phase 0 scalar object types only (#10)."""

    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    # Entity reference is represented by object_entity_id (object_type stays None).
    # Reserved for later phases: duration, currency, percentage, enum.


class FactStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"  # replaced by a newer fact
    RETRACTED = "retracted"    # source explicitly withdrew / declared it wrong


# --- ScanRun -----------------------------------------------------------------

class ScanRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# --- Drift (#16, #18) --------------------------------------------------------

class TargetType(StrEnum):
    """Polymorphic drift target (#17)."""

    FACT = "fact"
    DOCUMENT = "document"


class DriftType(StrEnum):
    CONFLICT = "conflict"
    POSSIBLY_STALE = "possibly_stale"
    CONFIRMED_STALE = "confirmed_stale"
    #: A currently-effective document quotes a value verbatim from an
    #: explicitly superseded edition while the chain head carries a
    #: different value (design 04 §11, rule R16).
    REUSED_STALE_VALUE = "reused_stale_value"
    SUPERSEDED = "superseded"
    DUPLICATE = "duplicate"


class Severity(StrEnum):
    WARNING = "warning"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AIImpactLevel(StrEnum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DriftStatus(StrEnum):
    OPEN = "open"
    IGNORED = "ignored"
    RESOLVED = "resolved"


# --- Resolution (#27) --------------------------------------------------------

class ResolutionDecision(StrEnum):
    ACCEPT_NEWER = "accept_newer"
    KEEP_OLD = "keep_old"
    MANUAL_OVERRIDE = "manual_override"
    FALSE_POSITIVE = "false_positive"


class ResolutionScope(StrEnum):
    """Phase 0 only supports single; pattern is reserved (#27)."""

    SINGLE = "single"


class ReasonCode(StrEnum):
    """Controlled vocabulary for Resolution.reason_code (#27, #28).

    Phase 0 keeps one flat list across all decisions; free-text detail goes
    into ``reason``. The codes feed future false-positive analysis and rule
    tuning — no model training is claimed in Phase 0.
    """

    SOURCE_UPDATED = "source_updated"            # accept_newer: source was revised
    NEWER_VERSION = "newer_version"              # accept_newer: explicit newer version
    DUPLICATE_CONFIRMED = "duplicate_confirmed"  # accept_newer: names are the same entity
    STILL_VALID = "still_valid"                  # keep_old: the old fact still holds
    LOWER_AUTHORITY = "lower_authority"          # keep_old: the new source is less authoritative
    MULTI_VALUED = "multi_valued"                # false_positive: tiered / co-existing values
    EXTRACTION_ERROR = "extraction_error"        # false_positive: the LLM mis-extracted
    DETECTOR_NOISE = "detector_noise"            # false_positive: deterministic rule misfired
    MANUAL = "manual"                            # manual_override: human adjudication
    OTHER = "other"


# --- CI (#26) ----------------------------------------------------------------

class CIFailOn(StrEnum):
    NONE = "none"
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    WARNING = "warning"


#: Threshold rank for fail_on semantics (#26). Higher rank = stricter.
FAIL_ON_RANK: dict[CIFailOn, int] = {
    CIFailOn.NONE: 0,
    CIFailOn.WARNING: 1,
    CIFailOn.MEDIUM: 2,
    CIFailOn.HIGH: 3,
    CIFailOn.CRITICAL: 4,
}

#: Drift severity rank aligned with the fail_on thresholds.
SEVERITY_RANK: dict[Severity, int] = {
    Severity.WARNING: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
