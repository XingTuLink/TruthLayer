"""Immutable, append-only snapshots (#29, #36).

A snapshot freezes the set of active facts/entities at scan time. This
module intentionally exposes only a *write* operation: snapshots are never
updated or deleted by application code.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from truthlayer.db.orm import Snapshot, SnapshotEntity, SnapshotFact


def write_snapshot(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    scan_run_id: uuid.UUID,
    knowledge_hash: str,
    fact_ids: Iterable[uuid.UUID],
    entity_ids: Iterable[uuid.UUID],
) -> Snapshot:
    fact_ids = list(fact_ids)
    entity_ids = list(entity_ids)

    snapshot = Snapshot(
        workspace_id=workspace_id,
        scan_run_id=scan_run_id,
        knowledge_hash=knowledge_hash,
        fact_count=len(fact_ids),
        entity_count=len(entity_ids),
    )
    session.add(snapshot)
    session.flush()

    session.add_all(
        SnapshotFact(snapshot_id=snapshot.id, fact_id=fact_id)
        for fact_id in fact_ids
    )
    session.add_all(
        SnapshotEntity(snapshot_id=snapshot.id, entity_id=entity_id)
        for entity_id in entity_ids
    )
    session.flush()
    return snapshot
