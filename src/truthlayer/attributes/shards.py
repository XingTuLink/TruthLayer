"""Build per-subject predicate shards from KnowledgeState (design §4).

Conflict detection only ever compares facts of ONE subject, so attribute
clustering for the conflict path is sharded by subject. A descriptor carries
everything the model needs to understand a predicate: real value samples with
their units/currency/tax treatment and the documents they come from.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from truthlayer.detection.state import FactView, KnowledgeState

#: Descriptors per LLM request are capped conservatively; a subject exceeding
#: the cap is split (design §12) — the structural cross-attribute summary
#: guarantees cross-split pairs stay visible, so this is a performance knob,
#: not a correctness boundary.
DEFAULT_PREDICATE_BUDGET = 60
#: Prompt v2 asks the model to normalize same-predicate surface variants, so
#: the cap is larger than the original 3 and duplicate renders are skipped:
#: every distinct short value must stand a chance of being co-judged.
MAX_SAMPLES_PER_PREDICATE = 6


@dataclass
class PredicateDescriptor:
    #: Original surface form of the first occurrence.
    predicate: str
    samples: list[str] = field(default_factory=list)
    source_docs: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)


@dataclass
class SubjectShard:
    subject_id: uuid.UUID
    subject_name: str
    subject_type: str | None
    #: Deduplicated descriptors, keyed by predicate.casefold().
    predicates: list[PredicateDescriptor]
    #: Eligible facts keyed by predicate.casefold().
    facts_by_predicate: dict[str, list[FactView]]
    #: True when this shard was split off an oversized subject (audit only).
    split: bool = False


def _render_sample(fact: FactView) -> str:
    if fact.object_entity_id is not None:
        return fact.object_entity_name or str(fact.object_entity_id)
    parts = [str(fact.object_value)]
    if fact.measure_unit:
        parts.append(fact.measure_unit)
    if fact.currency:
        parts.append(fact.currency)
    if fact.tax_basis == "inclusive":
        parts.append("含税")
    elif fact.tax_basis == "exclusive":
        parts.append("不含税")
    elif fact.tax_basis == "unknown":
        parts.append("税口径未说明")
    return " ".join(parts)


def build_shards(
    state: KnowledgeState,
    enabled_source_types: frozenset[str],
) -> tuple[list[SubjectShard], frozenset[uuid.UUID]]:
    """Group pricing-eligible facts into per-subject shards.

    Returns the shards (only subjects with >=2 distinct predicates need the
    LLM; single-predicate subjects are still represented by their eligible
    fact ids so the detector applies A×text/anchor rules uniformly) and the
    full set of eligible fact ids.
    """
    entity_types = {e.id: e.entity_type for e in state.entities}

    by_subject: dict[uuid.UUID, list[FactView]] = defaultdict(list)
    eligible_fact_ids: set[uuid.UUID] = set()
    for fact in state.facts:
        doc = state.fact_document(fact)
        if doc is None or doc.source_type not in enabled_source_types:
            continue
        by_subject[fact.subject_id].append(fact)
        eligible_fact_ids.add(fact.id)

    shards: list[SubjectShard] = []
    for subject_id, facts in by_subject.items():
        predicates: dict[str, PredicateDescriptor] = {}
        order: list[str] = []
        facts_by_predicate: dict[str, list[FactView]] = defaultdict(list)
        docs_seen: dict[str, set[str]] = defaultdict(set)
        samples_seen: dict[str, set[str]] = defaultdict(set)
        subject_name = facts[0].subject_name

        for fact in sorted(facts, key=lambda f: (f.predicate, str(f.id))):
            key = fact.predicate.strip().casefold()
            facts_by_predicate[key].append(fact)
            if key not in predicates:
                predicates[key] = PredicateDescriptor(predicate=fact.predicate)
                order.append(key)
            descriptor = predicates[key]
            doc = state.fact_document(fact)
            if doc is not None:
                docs_seen[key].add(doc.filename)
                if doc.source_type not in descriptor.source_types:
                    descriptor.source_types.append(doc.source_type)
            rendered = _render_sample(fact)
            if (
                rendered not in samples_seen[key]
                and len(descriptor.samples) < MAX_SAMPLES_PER_PREDICATE
            ):
                descriptor.samples.append(rendered)
                samples_seen[key].add(rendered)

        for key in order:
            predicates[key].source_docs = sorted(docs_seen[key])

        shards.append(
            SubjectShard(
                subject_id=subject_id,
                subject_name=subject_name,
                subject_type=entity_types.get(subject_id),
                predicates=[predicates[k] for k in order],
                facts_by_predicate=dict(facts_by_predicate),
            )
        )

    shards.sort(key=lambda s: s.subject_name)
    return shards, frozenset(eligible_fact_ids)


def pack_shards(
    shards: list[SubjectShard],
    budget: int = DEFAULT_PREDICATE_BUDGET,
) -> list[list[SubjectShard]]:
    """Pack subjects into LLM requests without splitting a subject.

    Subjects over the budget are split by source document (design §12);
    cross-split misses are recovered by the structural audit channel.
    """
    packs: list[list[SubjectShard]] = []
    current: list[SubjectShard] = []
    current_size = 0

    def flush() -> None:
        nonlocal current, current_size
        if current:
            packs.append(current)
            current = []
            current_size = 0

    for shard in shards:
        if len(shard.predicates) < 2:
            continue  # nothing to partition
        if len(shard.predicates) > budget:
            flush()
            for piece in _split_oversized(shard, budget):
                packs.append([piece])
            continue
        if current_size + len(shard.predicates) > budget:
            flush()
        current.append(shard)
        current_size += len(shard.predicates)
    flush()
    return packs


def _split_oversized(
    shard: SubjectShard, budget: int
) -> list[SubjectShard]:
    """Split by source document, then greedily balance predicate counts."""
    groups: dict[str, list[str]] = defaultdict(list)
    for descriptor in shard.predicates:
        first_doc = descriptor.source_docs[0] if descriptor.source_docs else ""
        groups[first_doc].append(descriptor.predicate.strip().casefold())

    pieces: list[SubjectShard] = []
    bucket: list[str] = []
    bucket_size = 0
    for keys in groups.values():
        if bucket and bucket_size + len(keys) > budget:
            pieces.append(_piece(shard, bucket))
            bucket, bucket_size = [], 0
        bucket.extend(keys)
        bucket_size += len(keys)
    if bucket:
        pieces.append(_piece(shard, bucket))
    return pieces


def _piece(shard: SubjectShard, predicate_keys: list[str]) -> SubjectShard:
    wanted = set(predicate_keys)
    return SubjectShard(
        subject_id=shard.subject_id,
        subject_name=shard.subject_name,
        subject_type=shard.subject_type,
        predicates=[
            descriptor
            for descriptor in shard.predicates
            if descriptor.predicate.strip().casefold() in wanted
        ],
        facts_by_predicate={
            key: facts
            for key, facts in shard.facts_by_predicate.items()
            if key in wanted
        },
        split=True,
    )
