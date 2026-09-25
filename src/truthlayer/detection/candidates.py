"""DriftCandidate — the pure-domain detector output (#16, #55).

Detectors return candidates; the service owns fingerprint dedupe and
persistence. A fingerprint identifies "the same knowledge problem" across
scans so open/ignored/resolved drifts are not re-reported (Remember loop).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from truthlayer.domain.enums import (
    AIImpactLevel,
    DriftType,
    Severity,
    TargetType,
)


@dataclass(frozen=True)
class DriftCandidate:
    detector_type: str
    drift_type: DriftType
    target_type: TargetType
    target_id: uuid.UUID
    severity: Severity
    ai_impact_level: AIImpactLevel
    confidence: float
    subject_entity_id: uuid.UUID | None = None
    predicate: str | None = None
    old_fact_id: uuid.UUID | None = None
    new_fact_id: uuid.UUID | None = None
    old_document_id: uuid.UUID | None = None
    new_document_id: uuid.UUID | None = None
    effective_at: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    #: Stable identity parts of "the same problem"; see ``fingerprint``.
    fingerprint_key: tuple[Any, ...] = ()

    def fingerprint(self) -> str:
        """Deterministic SHA-256 over the candidate's identity key.

        Components are ordered tuples of JSON-able scalars / UUIDs so the
        fingerprint is independent of dict/row ordering.
        """
        normalized = json.dumps(
            list(self.fingerprint_key),
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# --- Deterministic comparison helpers ---------------------------------------

def windows_overlap(
    a_from: date | None,
    a_to: date | None,
    b_from: date | None,
    b_to: date | None,
) -> bool:
    """Half-open-ish inclusive date windows; None means an open boundary.

    Two windows overlap unless one ends strictly before the other starts.
    Windows with no dates at all (both fully open) overlap.
    """
    if a_to is not None and b_from is not None and a_to < b_from:
        return False
    if b_to is not None and a_from is not None and b_to < a_from:
        return False
    return True


def canonical_object(
    value: Any,
    object_type: str | None,
    object_entity_id: uuid.UUID | None,
) -> Any:
    """Normalize a fact object for equality / Canonical-Fact comparison (#12).

    - entity objects compare by entity id;
    - 149 (int) and 149.0 (float) are the same number;
    - strings are whitespace-stripped but keep case (case changes can be
      semantically real);
    - dates are ISO strings as stored in JSONB.
    """
    if object_entity_id is not None:
        return ("entity", str(object_entity_id))
    if object_type == "number" and isinstance(value, float) and value.is_integer():
        value = int(value)
    if object_type == "string" and isinstance(value, str):
        value = value.strip()
    return ("scalar", object_type, value)


def objects_equal(
    a_value: Any,
    a_type: str | None,
    a_entity_id: uuid.UUID | None,
    b_value: Any,
    b_type: str | None,
    b_entity_id: uuid.UUID | None,
) -> bool:
    return canonical_object(
        a_value, a_type, a_entity_id
    ) == canonical_object(b_value, b_type, b_entity_id)
