"""Resolver validation tests: the model proposes, deterministic rules dispose."""

from __future__ import annotations

from truthlayer.attributes import AttributeResolver
from truthlayer.attributes.schemas import AttributeResolutionEnvelope

from .conftest import build_state, fact, policy_doc, pricing_doc


class FakeLLM:
    def __init__(self, payload: dict | Exception):
        self._payload = payload
        self.calls: list[dict] = []

    def generate_structured(
        self, *, input_text, output_schema, system_prompt=None, model=None
    ):
        self.calls.append({"input_text": input_text, "system_prompt": system_prompt})
        if isinstance(self._payload, Exception):
            raise self._payload
        return AttributeResolutionEnvelope.model_validate(self._payload)


def _measure_cluster(
    *, name="年度含税价格", aliases, value_kind="measure", evidence="价格"
):
    return {
        "canonical_name": name,
        "definition": "按年按企业计的含税价格",
        "value_kind": value_kind,
        "aliases": [
            {"predicate": alias, "evidence_span": evidence, "confidence": 0.9}
            for alias in aliases
        ],
    }


def _envelope(subject: str, clusters: list[dict]) -> dict:
    return {"subjects": [{"subject": subject, "clusters": clusters}]}


def _two_price_facts(unit_b="元/年·企业"):
    return [
        fact(
            "a", predicate="2026年价格", value=23800,
            unit="元/年·企业", currency="CNY", tax_basis="inclusive",
            document="06_总部价表.md",
        ),
        fact(
            "b", predicate="渠道结算价", value=22800,
            unit=unit_b, currency="CNY", tax_basis="inclusive",
            document="02_渠道价格.md",
        ),
    ]


def _pricing_docs():
    return [pricing_doc("06_总部价表.md"), pricing_doc("02_渠道价格.md")]


def test_trusted_measure_merge_creates_identity_b():
    state = build_state(_two_price_facts(), _pricing_docs())
    llm = FakeLLM(
        _envelope(
            "云客服专业版",
            [_measure_cluster(aliases=["2026年价格", "渠道结算价"])],
        )
    )
    resolution = AttributeResolver(llm).resolve(state)

    identity = resolution.pair_identity(
        state.facts[0].subject_id,
        state.facts[1].subject_id,
        state.facts[0].predicate,
        state.facts[1].predicate,
    )
    assert identity.kind == "B"
    assert identity.trusted is True
    assert identity.value_kind == "measure"
    assert identity.canonical_name == "年度含税价格"
    assert resolution.notes == []


def test_anchor_dimension_conflict_forces_split_no_identity_b():
    state = build_state(
        _two_price_facts(unit_b="元/人天"), _pricing_docs()
    )
    llm = FakeLLM(
        _envelope(
            "云客服专业版",
            [_measure_cluster(aliases=["2026年价格", "渠道结算价"])],
        )
    )
    resolution = AttributeResolver(llm).resolve(state)

    identity = resolution.pair_identity(
        state.facts[0].subject_id,
        state.facts[1].subject_id,
        state.facts[0].predicate,
        state.facts[1].predicate,
    )
    assert identity.kind is None


def test_unverifiable_evidence_keeps_merge_untrusted():
    state = build_state(_two_price_facts(), _pricing_docs())
    llm = FakeLLM(
        _envelope(
            "云客服专业版",
            [
                _measure_cluster(
                    aliases=["2026年价格", "渠道结算价"],
                    evidence="根本不存在的原文片段",
                )
            ],
        )
    )
    resolution = AttributeResolver(llm).resolve(state)
    identity = resolution.pair_identity(
        state.facts[0].subject_id,
        state.facts[1].subject_id,
        state.facts[0].predicate,
        state.facts[1].predicate,
    )
    assert identity.kind == "B"
    assert identity.trusted is False


def test_alias_not_matching_input_is_discarded():
    state = build_state(_two_price_facts(), _pricing_docs())
    llm = FakeLLM(
        _envelope(
            "云客服专业版",
            [
                _measure_cluster(
                    aliases=["2026年价格", "渠道结算价", "模型臆造的属性"]
                )
            ],
        )
    )
    resolution = AttributeResolver(llm).resolve(state)
    # The two real aliases still merge; the hallucinated one is recorded.
    identity = resolution.pair_identity(
        state.facts[0].subject_id,
        state.facts[1].subject_id,
        state.facts[0].predicate,
        state.facts[1].predicate,
    )
    assert identity.kind == "B"
    assert any("discarded alias" in note for note in resolution.notes)


def test_unknown_subject_section_is_ignored():
    state = build_state(_two_price_facts(), _pricing_docs())
    llm = FakeLLM(
        _envelope("另一个不存在的主体", [_measure_cluster(aliases=["x", "y"])])
    )
    resolution = AttributeResolver(llm).resolve(state)
    assert any("unknown subject" in note for note in resolution.notes)
    assert resolution.bindings == {}


def test_llm_failure_degrades_without_throwing():
    state = build_state(_two_price_facts(), _pricing_docs())
    llm = FakeLLM(RuntimeError("provider down"))
    resolution = AttributeResolver(llm).resolve(state)
    assert resolution.bindings == {}
    assert any("clustering failed" in note for note in resolution.notes)


def test_non_pricing_facts_are_not_eligible():
    facts = _two_price_facts()
    state = build_state(
        facts,
        [policy_doc("06_总部价表.md"), policy_doc("02_渠道价格.md")],
    )
    llm = FakeLLM(_envelope("云客服专业版", []))
    resolution = AttributeResolver(llm).resolve(state)
    assert resolution.eligible_fact_ids == frozenset()
    assert llm.calls == []  # no shards at all


# -- prompt v2: same-predicate text-value equivalence -------------------------

def _text_cluster(name, aliases, *, equivalent_text=()):
    return {
        "canonical_name": name,
        "definition": "服务包含的响应形态",
        "value_kind": "enumeration",
        "aliases": [
            {"predicate": alias, "evidence_span": "服务", "confidence": 0.9}
            for alias in aliases
        ],
        "equivalent_text_values": [list(pair) for pair in equivalent_text],
    }


def _variant_docs():
    return [pricing_doc("d1.md"), pricing_doc("d2.md")]


def _same_predicate_variant_facts():
    return [
        fact(
            "a", predicate="包含服务", value="7×24小时响应",
            object_type="string", document="d1.md",
        ),
        fact(
            "b", predicate="包含服务", value="7×24小时响应服务",
            object_type="string", document="d2.md",
        ),
        # Second predicate so the subject reaches the LLM at all.
        fact(
            "c", predicate="响应方式", value="在线客服",
            object_type="string", document="d1.md",
        ),
    ]


def test_singleton_text_cluster_absorbs_value_equivalence():
    state = build_state(_same_predicate_variant_facts(), _variant_docs())
    llm = FakeLLM(
        _envelope(
            "云客服专业版",
            [
                _text_cluster(
                    "包含服务",
                    ["包含服务"],
                    equivalent_text=[("7×24小时响应", "7×24小时响应服务")],
                ),
                _text_cluster("响应方式", ["响应方式"]),
            ],
        )
    )
    resolution = AttributeResolver(llm).resolve(state)

    subject_id = state.facts[0].subject_id
    assert resolution.is_text_equivalent(
        subject_id, "7×24小时响应", "7×24小时响应服务"
    )
    # Singleton clusters still create no predicate bindings.
    assert resolution.bindings == {}
    assert resolution.notes == []


def test_equivalent_text_pair_with_unseen_value_is_discarded():
    state = build_state(_same_predicate_variant_facts(), _variant_docs())
    llm = FakeLLM(
        _envelope(
            "云客服专业版",
            [
                _text_cluster(
                    "包含服务",
                    ["包含服务"],
                    equivalent_text=[("7×24小时响应", "模型臆造的全天值守")],
                ),
                _text_cluster("响应方式", ["响应方式"]),
            ],
        )
    )
    resolution = AttributeResolver(llm).resolve(state)

    subject_id = state.facts[0].subject_id
    assert not resolution.is_text_equivalent(
        subject_id, "7×24小时响应", "模型臆造的全天值守"
    )
    assert any(
        "discarded equivalent_text pair" in note for note in resolution.notes
    )


def test_deterministic_confluence_key_merges_surface_variants():
    state = build_state(
        [
            fact(
                "a", subject="甲", predicate="结算价", value=100,
                unit="元/年", currency="CNY", tax_basis="inclusive",
                document="d1.md",
            ),
            fact(
                "b", subject="乙", predicate="结算价。", value=100,
                unit="元/年", currency="CNY", tax_basis="inclusive",
                document="d2.md",
            ),
        ],
        [pricing_doc("d1.md"), pricing_doc("d2.md")],
    )
    llm = FakeLLM(
        {
            "subjects": [
                {
                    "subject": "甲",
                    "clusters": [
                        _measure_cluster(name="结算价", aliases=["结算价"])
                    ],
                },
                {
                    "subject": "乙",
                    "clusters": [
                        _measure_cluster(name="结算价。", aliases=["结算价。"])
                    ],
                },
            ]
        }
    )
    resolution = AttributeResolver(llm).resolve(state)
    # Singleton clusters produce no bindings, but canonical confluence keys
    # are name-normalized to the same workspace-level attribute.
    keys = set(resolution.canonicals)
    # Singletons are skipped (<2 aliases), so no canonicals here; verify the
    # key function directly instead.
    assert keys == set()
    from truthlayer.attributes import AttributeResolution as AR

    assert AR.confluence_key("结算价", "measure", "元/年") == AR.confluence_key(
        "结算价。", "measure", "元/年"
    )
