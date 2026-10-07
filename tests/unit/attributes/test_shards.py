"""Shard construction, gray-release filtering and token-budget splitting."""

from __future__ import annotations

from truthlayer.attributes.shards import build_shards, pack_shards

from .conftest import build_state, fact, policy_doc, pricing_doc


def test_shards_dedupe_predicates_and_attach_samples():
    facts = [
        fact(
            "a", predicate="价格", value=100, unit="元/年",
            currency="CNY", tax_basis="inclusive", document="d1.md",
        ),
        fact(
            "b", predicate="价格", value=120, unit="元/年",
            currency="CNY", tax_basis="inclusive", document="d2.md",
        ),
        fact(
            "c", predicate="渠道结算价", value=90, unit="元/年",
            currency="CNY", tax_basis="inclusive", document="d2.md",
        ),
    ]
    state = build_state(facts, [pricing_doc("d1.md"), pricing_doc("d2.md")])
    shards, eligible = build_shards(state, frozenset({"pricing"}))

    assert len(shards) == 1
    shard = shards[0]
    assert {d.predicate for d in shard.predicates} == {"价格", "渠道结算价"}
    assert len(eligible) == 3
    descriptor = next(d for d in shard.predicates if d.predicate == "价格")
    assert set(descriptor.source_docs) == {"d1.md", "d2.md"}
    assert len(descriptor.samples) == 2


def test_single_predicate_subject_is_not_packed():
    facts = [
        fact("a", predicate="价格", value=100, unit="元/年", document="d1.md"),
        fact("b", predicate="价格", value=120, unit="元/年", document="d2.md"),
    ]
    state = build_state(facts, [pricing_doc("d1.md"), pricing_doc("d2.md")])
    shards, eligible = build_shards(state, frozenset({"pricing"}))
    assert len(eligible) == 2
    assert pack_shards(shards) == []


def test_policy_documents_are_filtered_out():
    facts = [
        fact("a", predicate="价格", value=100, unit="元/年", document="d1.md"),
    ]
    state = build_state(facts, [policy_doc("d1.md")])
    shards, eligible = build_shards(state, frozenset({"pricing"}))
    assert shards == []
    assert eligible == frozenset()


def test_oversized_shard_is_split_by_source_document():
    facts = []
    for index in range(5):
        facts.append(
            fact(
                f"a{index}", predicate=f"价格A{index}", value=index,
                unit="元/年", document="big1.md",
            )
        )
    for index in range(5):
        facts.append(
            fact(
                f"b{index}", predicate=f"价格B{index}", value=index,
                unit="元/年", document="big2.md",
            )
        )
    state = build_state(facts, [pricing_doc("big1.md"), pricing_doc("big2.md")])
    shards, _ = build_shards(state, frozenset({"pricing"}))
    packs = pack_shards(shards, budget=6)

    # One subject split into two single-subject packs (one per source doc).
    assert len(packs) == 2
    assert all(len(pack) == 1 for pack in packs)
    assert all(pack[0].split for pack in packs)
    predicates = {
        d.predicate for pack in packs for d in pack[0].predicates
    }
    assert len(predicates) == 10


def test_small_subjects_are_packed_together_but_never_split():
    facts = []
    for subject in ("甲", "乙"):
        for index in range(3):
            facts.append(
                fact(
                    f"{subject}{index}", subject=subject,
                    predicate=f"属性{index}", value=index,
                    unit="元/年", document=f"{subject}.md",
                )
            )
    state = build_state(
        facts, [pricing_doc("甲.md"), pricing_doc("乙.md")]
    )
    shards, _ = build_shards(state, frozenset({"pricing"}))
    packs = pack_shards(shards, budget=10)
    assert len(packs) == 1
    assert {s.subject_name for s in packs[0]} == {"甲", "乙"}
