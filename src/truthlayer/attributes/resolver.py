"""Run attribute clustering LLM calls and turn them into a validated
``AttributeResolution`` (design 04, phase 1).

Trust model, enforced here rather than left to the prompt:

* alias texts must match real input predicates verbatim (case-folded);
* evidence spans must be locatable in the predicate/value surface text;
* measure merges require one shared unit/currency dimension across every
  alias, else the merge is split or downgraded to untrusted;
* untrusted merges still exist (so pairs become *pending review*, never
  silently lost) but can never produce a blocking drift.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from truthlayer.attributes.anchors import normalize_dimension
from truthlayer.attributes.model import AttributeResolution, CanonicalAttribute, _Binding
from truthlayer.attributes.prompts import (
    ATTRIBUTE_PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
    render_subject_section,
)
from truthlayer.attributes.schemas import AttributeResolutionEnvelope
from truthlayer.attributes.shards import (
    DEFAULT_PREDICATE_BUDGET,
    SubjectShard,
    build_shards,
    pack_shards,
)
from truthlayer.detection.state import KnowledgeState
from truthlayer.providers.llm import LLMProvider

#: phase-1 gray release (design §12 decision 1).
DEFAULT_SOURCE_TYPES = frozenset({"pricing"})


class AttributeResolver:
    def __init__(
        self,
        llm: LLMProvider,
        *,
        enabled_source_types: frozenset[str] = DEFAULT_SOURCE_TYPES,
        predicate_budget: int = DEFAULT_PREDICATE_BUDGET,
    ) -> None:
        self.llm = llm
        self.enabled_source_types = enabled_source_types
        self.predicate_budget = predicate_budget

    @property
    def prompt_version(self) -> str:
        return ATTRIBUTE_PROMPT_VERSION

    def resolve(self, state: KnowledgeState) -> AttributeResolution:
        shards, eligible = build_shards(state, self.enabled_source_types)
        resolution = AttributeResolution(
            eligible_fact_ids=eligible,
            enabled_source_types=self.enabled_source_types,
        )
        if not shards:
            return resolution

        shard_by_name = {shard.subject_name.strip().casefold(): shard for shard in shards}
        packs = pack_shards(shards, self.predicate_budget)
        split_subjects = {s.subject_id for pack in packs for s in pack if s.split}
        for subject_id in split_subjects:
            resolution.notes.append(
                f"subject {subject_id} exceeded predicate budget and was split"
            )

        for pack in packs:
            sections = [
                render_subject_section(
                    subject_name=shard.subject_name,
                    subject_type=shard.subject_type,
                    predicates=[
                        {
                            "predicate": descriptor.predicate,
                            "samples": descriptor.samples,
                            "source_docs": descriptor.source_docs,
                            "source_types": descriptor.source_types,
                        }
                        for descriptor in shard.predicates
                    ],
                )
                for shard in pack
            ]
            try:
                envelope = self.llm.generate_structured(
                    input_text=build_user_prompt(sections),
                    output_schema=AttributeResolutionEnvelope,
                    system_prompt=SYSTEM_PROMPT,
                )
            except Exception as exc:  # noqa: BLE001 - degradation, not fatal
                # No bindings for this pack: identity-A detection is untouched
                # and the structural summary still surfaces measure pairs.
                resolution.notes.append(
                    f"attribute clustering failed for "
                    f"{[s.subject_name for s in pack]}: {type(exc).__name__}"
                )
                continue

            pack_names = {s.subject_name.strip().casefold() for s in pack}
            for section in envelope.subjects:
                name_key = section.subject.strip().casefold()
                if name_key not in pack_names:
                    resolution.notes.append(
                        f"clustering returned unknown subject {section.subject!r}"
                    )
                    continue
                shard = shard_by_name[name_key]
                self._absorb_subject(shard, section, resolution)

        return resolution

    # -- internals ----------------------------------------------------------

    def _absorb_subject(self, shard: SubjectShard, section, resolution: AttributeResolution) -> None:
        available: dict[str, str] = {
            descriptor.predicate.strip().casefold(): descriptor.predicate
            for descriptor in shard.predicates
        }
        surfaces: dict[str, str] = {
            key: self._surface_text(shard, key) for key in available
        }
        seen_predicates: set[str] = set()

        for cluster in section.clusters:
            alias_keys: list[str] = []
            alias_trusted: dict[str, bool] = {}
            for alias in cluster.aliases:
                key = alias.predicate.strip().casefold()
                if key not in available or key in seen_predicates:
                    resolution.notes.append(
                        f"discarded alias {alias.predicate!r} for subject "
                        f"{shard.subject_name!r} (unknown or duplicate)"
                    )
                    continue
                seen_predicates.add(key)
                span = alias.evidence_span or ""
                alias_trusted[key] = self._span_supported(span, surfaces[key])
                alias_keys.append(key)

            if len(alias_keys) < 2:
                continue  # singleton or all-invalid: no merge proposed

            groups, dim_of = self._dimension_groups(shard, alias_keys, cluster.value_kind)
            for group_keys, unit_dimension in groups:
                if len(group_keys) < 2:
                    continue
                canonical_key = AttributeResolution.confluence_key(
                    cluster.canonical_name, cluster.value_kind, unit_dimension
                )
                resolution.canonicals.setdefault(
                    canonical_key,
                    CanonicalAttribute(
                        key=canonical_key,
                        canonical_name=cluster.canonical_name,
                        definition=cluster.definition,
                        value_kind=cluster.value_kind,
                        unit_dimension=unit_dimension,
                    ),
                )
                for key in group_keys:
                    trusted = alias_trusted.get(key, False)
                    if cluster.value_kind == "measure" and unit_dimension is not None:
                        trusted = trusted and self._alias_has_dimension(
                            shard, key, unit_dimension
                        )
                    resolution.bindings[(shard.subject_id, key)] = _Binding(
                        canonical_key=canonical_key,
                        value_kind=cluster.value_kind,
                        unit_dimension=unit_dimension,
                        trusted=trusted,
                    )

            # Aliases that landed on different measure dimensions are a
            # rejected merge — record every cross-dimension pair so the
            # detector can route it to cross-attribute review (Golden 11).
            if cluster.value_kind == "measure":
                dimmed = [k for k in alias_keys if dim_of.get(k) is not None]
                for i, left in enumerate(dimmed):
                    for right in dimmed[i + 1:]:
                        if dim_of[left] != dim_of[right]:
                            key_a, key_b = sorted((left, right))
                            resolution.rejected_merges.add(
                                (shard.subject_id, key_a, key_b)
                            )

            for pair in cluster.equivalent_text_values:
                if cluster.value_kind in {"text", "enumeration"}:
                    resolution.text_equivalences.add(
                        (
                            shard.subject_id,
                            pair[0].strip().casefold(),
                            pair[1].strip().casefold(),
                        )
                    )

    def _dimension_groups(
        self,
        shard: SubjectShard,
        alias_keys: list[str],
        value_kind: str,
    ) -> tuple[list[tuple[list[str], str | None]], dict[str, str | None]]:
        """Split a proposed cluster on contradictory measure dimensions.

        Returns ((alias_keys, shared unit dimension) groups, dim-per-alias).
        Non-measure clusters pass through untouched with dimension None.
        """
        dim_of: dict[str, str | None] = {key: None for key in alias_keys}
        if value_kind != "measure":
            return [(alias_keys, None)], dim_of

        dims_by_alias: dict[str, set[str]] = {}
        for key in alias_keys:
            dims = {
                normalize_dimension(f.measure_unit)
                for f in shard.facts_by_predicate.get(key, [])
                if normalize_dimension(f.measure_unit) is not None
            }
            dims_by_alias[key] = dims

        dimension_counts: Counter[str] = Counter(
            dim for dims in dims_by_alias.values() for dim in dims
        )
        if not dimension_counts:
            # Measure cluster with no parseable units anywhere: T-02 — keep
            # one group with None dimension; bindings stay untrusted.
            return [(alias_keys, None)], dim_of

        groups: dict[str, list[str]] = defaultdict(list)
        unanchored: list[str] = []
        for key in alias_keys:
            dims = dims_by_alias[key]
            if not dims:
                unanchored.append(key)
                continue
            # Pick the dimension this alias most often shares across aliases;
            # an alias spanning two dimensions joins its dominant one and the
            # remainder falls through to the structural summary.
            chosen = max(dims, key=lambda d: dimension_counts[d])
            dim_of[key] = chosen
            groups[chosen].append(key)

        result = [(keys, dim) for dim, keys in groups.items() if len(keys) >= 2]
        # Aliases whose dimension matched no other alias cannot merge here;
        # unanchored aliases attach to the largest group as untrusted so the
        # pair surfaces as pending rather than disappearing.
        if unanchored and result:
            biggest = max(result, key=lambda item: len(item[1]))
            biggest[1].extend(unanchored)
        return result, dim_of

    @staticmethod
    def _alias_has_dimension(
        shard: SubjectShard, key: str, unit_dimension: str
    ) -> bool:
        facts = shard.facts_by_predicate.get(key, [])
        anchored = [normalize_dimension(f.measure_unit) for f in facts]
        anchored = [dim for dim in anchored if dim is not None]
        return bool(anchored) and all(dim == unit_dimension for dim in anchored)

    @staticmethod
    def _surface_text(shard: SubjectShard, key: str) -> str:
        descriptor = next(
            d for d in shard.predicates if d.predicate.strip().casefold() == key
        )
        return " ".join([descriptor.predicate, *descriptor.samples]).casefold()

    @staticmethod
    def _span_supported(span: str, surface: str) -> bool:
        if not span:
            return False
        return "".join(span.split()).casefold() in "".join(surface.split())
