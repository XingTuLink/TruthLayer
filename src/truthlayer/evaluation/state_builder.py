"""Build a deterministic :class:`KnowledgeState` from a QA case.

Detectors read only frozen view dataclasses, so a case can be evaluated
without a database. All ids are derived from ``uuid5`` over (case id, local
id), making fingerprints stable across machines and runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from truthlayer.detection.context import DetectionContext
from truthlayer.detection.scoring import at_utc_midday
from truthlayer.detection.state import (
    DocumentView,
    EntityView,
    FactView,
    KnowledgeState,
)
from truthlayer.evaluation.schema import QACase

_NAMESPACE = uuid.NAMESPACE_URL


def _derived(case_id: str, kind: str, local_id: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"truthlayer-qa:{case_id}:{kind}:{local_id}")


def _midday(day) -> datetime | None:
    return at_utc_midday(day) if day is not None else None


@dataclass(frozen=True)
class CaseWorld:
    state: KnowledgeState
    context: DetectionContext
    entity_ids: dict[str, uuid.UUID]
    document_ids: dict[str, uuid.UUID]
    document_filenames: dict[str, str]


def build_world(case: QACase) -> CaseWorld:
    workspace_id = _derived(case.id, "workspace", "main")

    entity_ids = {e.id: _derived(case.id, "entity", e.id) for e in case.entities}
    document_ids = {
        d.id: _derived(case.id, "document", d.id) for d in case.documents
    }

    documents: dict[uuid.UUID, DocumentView] = {}
    for doc in case.documents:
        documents[document_ids[doc.id]] = DocumentView(
            id=document_ids[doc.id],
            group_id=(
                _derived(case.id, "group", doc.group)
                if doc.group is not None
                else None
            ),
            previous_version_id=(
                document_ids[doc.previous_version_of]
                if doc.previous_version_of is not None
                else None
            ),
            filename=doc.filename,
            source_type=doc.source_type,
            authority_score=doc.authority,
            parsed_at=_midday(doc.parsed_at),
        )

    entity_name = {e.id: e.name for e in case.entities}
    entities = [
        EntityView(
            id=entity_ids[e.id],
            canonical_name=e.name,
            entity_type=e.type,
            embedding=tuple(e.embedding) if e.embedding is not None else None,
        )
        for e in case.entities
    ]

    facts: list[FactView] = []
    for fact in case.facts:
        if fact.value_type == "entity":
            object_entity_id = entity_ids[fact.object_entity]
            object_value = None
            object_type = None
        else:
            object_entity_id = None
            object_value = fact.value
            object_type = fact.value_type

        facts.append(
            FactView(
                id=_derived(case.id, "fact", fact.id),
                subject_id=entity_ids[fact.subject],
                subject_name=entity_name[fact.subject],
                predicate=fact.predicate,
                object_value=object_value,
                object_type=object_type,
                object_entity_id=object_entity_id,
                object_entity_name=(
                    entity_name[fact.object_entity]
                    if fact.object_entity is not None
                    else None
                ),
                valid_from=fact.valid_from,
                valid_to=fact.valid_to,
                observed_at=fact.observed_at,
                confidence=0.9,
                measure_unit=fact.measure_unit,
                document_id=(
                    document_ids[fact.source] if fact.source is not None else None
                ),
            )
        )

    state = KnowledgeState(
        workspace_id=workspace_id,
        facts=facts,
        documents=documents,
        entities=entities,
    )

    rules = case.rules
    context = DetectionContext(
        as_of=case.as_of,
        stale_after_days=rules.stale_after_days,
        pricing_stale_days=rules.pricing_stale_days or rules.stale_after_days,
        severity_overrides=dict(rules.severity),
        multi_valued_predicates=frozenset(
            p.strip().casefold() for p in rules.multi_valued_predicates
        ),
        immutable_metadata_predicates=frozenset(
            p.strip().casefold()
            for p in rules.immutable_metadata_predicates
        ),
    )

    return CaseWorld(
        state=state,
        context=context,
        entity_ids=entity_ids,
        document_ids=document_ids,
        document_filenames={d.id: d.filename for d in case.documents},
    )
