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
