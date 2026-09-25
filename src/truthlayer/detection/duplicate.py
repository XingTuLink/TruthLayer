"""DuplicateDetector (#21, #12).

Embedding similarity only RECALLS candidate entity pairs; a duplicate is
confirmed solely by deterministic comparison:

different subject entities + same predicate + same canonical object
+ overlapping validity => the two entities were never merged and state the
same fact twice.

Identical (subject, predicate, object, window) rows are Canonical Facts
with multiple evidence — extraction already dedupes those, and this
detector never flags them.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from truthlayer.detection.candidates import (
    DriftCandidate,
    canonical_object,
    objects_equal,
    windows_overlap,
)
from truthlayer.detection.context import DetectionContext
from truthlayer.detection.scoring import (
    RECALL_CONFIDENCE,
    STRUCTURAL_CONFIDENCE,
    ai_impact_for,
)
from truthlayer.detection.state import EntityView, FactView, KnowledgeState
from truthlayer.domain.enums import DriftType, Severity, TargetType

NAME = "duplicate_detector"

#: Embedding recall threshold ONLY — never a decision threshold (#21).
SIMILARITY_RECALL = 0.85
#: Shortest token-set size (in chars) for containment recall.
CONTAINMENT_MIN_CHARS = 4


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", name)).strip().casefold()


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[\s_\-/，,、()（）]+", name) if t}


def cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _name_recall(a: EntityView, b: EntityView) -> str | None:
    """Return the recall reason if two entity names look like one entity."""
    name_a = _normalize_name(a.canonical_name)
    name_b = _normalize_name(b.canonical_name)
    if name_a == name_b:
        return "exact_normalized_name"

    tokens_a = _tokens(name_a)
    tokens_b = _tokens(name_b)
    if tokens_a and tokens_b:
        shorter, longer = sorted((tokens_a, tokens_b), key=len)
        if shorter <= longer and sum(len(t) for t in shorter) >= CONTAINMENT_MIN_CHARS:
            return "name_token_containment"
        union = tokens_a | tokens_b
        if union and len(tokens_a & tokens_b) / len(union) >= 0.6:
            return "name_token_overlap"
    return None


@dataclass
class DuplicateDetector:
    name: str = NAME

    def detect(
        self,
        state: KnowledgeState,
        context: DetectionContext,
    ) -> list[DriftCandidate]:
        facts_by_subject: dict = {}
        for fact in state.facts:
            facts_by_subject.setdefault(fact.subject_id, []).append(fact)

        # Document frequency of each (predicate, canonical object): how many
        # distinct subjects hold it. Generic facts ("unit = 座席/月", held by
        # three product lines) carry no identifying power and must not by
        # themselves prove two entities are duplicates.
        holders: dict[tuple, set] = defaultdict(set)
        for fact in state.facts:
            holders[
                (
                    fact.predicate.casefold(),
                    canonical_object(
                        fact.object_value,
                        fact.object_type,
                        fact.object_entity_id,
                    ),
                )
            ].add(fact.subject_id)

        candidates: list[DriftCandidate] = []
        seen_pairs: set[tuple] = set()
        for entity_a, entity_b in combinations(
            sorted(state.entities, key=lambda e: str(e.id)), 2
        ):
            recall = self._recall_pair(entity_a, entity_b)
            if recall is None:
                continue
            pair_candidates = self._compare_entities(
                entity_a,
                entity_b,
                facts_by_subject.get(entity_a.id, []),
                facts_by_subject.get(entity_b.id, []),
                recall,
                holders,
            )
            for candidate in pair_candidates:
                dedupe = tuple(sorted(candidate.fingerprint_key[1:]))
                if dedupe in seen_pairs:
                    continue
                seen_pairs.add(dedupe)
                candidates.append(candidate)
        return candidates

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _recall_pair(a: EntityView, b: EntityView) -> str | None:
        name_reason = _name_recall(a, b)
        if name_reason is not None:
            return name_reason
        if a.embedding is not None and b.embedding is not None:
            if cosine_similarity(a.embedding, b.embedding) >= SIMILARITY_RECALL:
                return "embedding_similarity"
        return None

    def _compare_entities(
        self,
        entity_a: EntityView,
        entity_b: EntityView,
        facts_a: list[FactView],
        facts_b: list[FactView],
        recall: str,
        holders: dict[tuple, set],
    ) -> list[DriftCandidate]:
        confidence = (
            STRUCTURAL_CONFIDENCE
            if recall == "exact_normalized_name"
            else RECALL_CONFIDENCE
        )
        shared: list[tuple[FactView, FactView]] = []
        for fact_a in facts_a:
            for fact_b in facts_b:
                if fact_a.predicate.casefold() != fact_b.predicate.casefold():
                    continue
                if not windows_overlap(
                    fact_a.valid_from,
                    fact_a.valid_to,
                    fact_b.valid_from,
                    fact_b.valid_to,
                ):
                    continue
                if not objects_equal(
                    fact_a.object_value,
                    fact_a.object_type,
                    fact_a.object_entity_id,
                    fact_b.object_value,
                    fact_b.object_type,
                    fact_b.object_entity_id,
                ):
                    continue
                shared.append((fact_a, fact_b))

        if not shared:
            return []

        # Confirmation requires either >=2 shared facts, or exactly one
        # identifying fact held by no other entity in the knowledge base.
        if len(shared) < 2:
            only = shared[0]
            key = (
                only[0].predicate.casefold(),
                canonical_object(
                    only[0].object_value,
                    only[0].object_type,
                    only[0].object_entity_id,
                ),
            )
            if len(holders.get(key, set())) > 2:
                return []

        out: list[DriftCandidate] = []
        for fact_a, fact_b in shared:
            old, new = sorted((fact_a, fact_b), key=lambda f: str(f.id))
            out.append(
                DriftCandidate(
                    detector_type=NAME,
                    drift_type=DriftType.DUPLICATE,
                    target_type=TargetType.FACT,
                    target_id=old.id,
                    severity=Severity.WARNING,
                    ai_impact_level=ai_impact_for(Severity.WARNING),
                    confidence=confidence,
                    subject_entity_id=old.subject_id,
                    predicate=old.predicate,
                    old_fact_id=old.id,
                    new_fact_id=new.id,
                    detail={
                        "reason": "unmerged_entities_same_fact",
                        "recall": recall,
                        "shared_fact_count": len(shared),
                        "entity_a": entity_a.canonical_name,
                        "entity_b": entity_b.canonical_name,
                        "predicate": old.predicate,
                        "object": old.object_entity_name
                        if old.object_entity_id is not None
                        else old.object_value,
                    },
                    fingerprint_key=(
                        DriftType.DUPLICATE.value,
                        old.predicate.casefold(),
                        *sorted((str(old.id), str(new.id))),
                    ),
                )
            )
        return out
