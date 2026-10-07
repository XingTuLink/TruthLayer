"""Gating matrix for attribute resolution phase 1 (design 04 §5-8, Golden
#1/#11/#12/#19/#24/#25).

The LLM only separates, asks or suppresses — it never convicts. Blocking
conflict candidates still come exclusively from literal identity A plus
deterministic measure anchors.
"""

from __future__ import annotations

from datetime import date

from truthlayer.attributes import AttributeResolver, ChannelKind
from truthlayer.attributes.schemas import AttributeResolutionEnvelope

from .conftest import build_state, make_document, make_fact


PRICE_DOC_A = "06_总部价表.md"
PRICE_DOC_B = "02_渠道价格.md"


class FakeLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    def generate_structured(
        self, *, input_text, output_schema, system_prompt=None, model=None
    ):
        return AttributeResolutionEnvelope.model_validate(self._payload)


def _pricing_docs():
    return [
        make_document(PRICE_DOC_A, source_type="pricing"),
        make_document(PRICE_DOC_B, source_type="pricing"),
    ]


def _policy_docs():
    return [
        make_document(PRICE_DOC_A, source_type="policy"),
        make_document(PRICE_DOC_B, source_type="policy"),
    ]


def _merge_envelope(
    canonical="年度含税价格",
    *,
    aliases,
    value_kind="measure",
    evidence="价格",
    subject="云客服专业版",
    equivalent_text=None,
):
    cluster = {
        "canonical_name": canonical,
        "definition": "按年按企业计的含税价格",
        "value_kind": value_kind,
        "aliases": [
            {"predicate": alias, "evidence_span": evidence, "confidence": 0.9}
            for alias in aliases
        ],
        "equivalent_text_values": equivalent_text or [],
    }
    return {"subjects": [{"subject": subject, "clusters": [cluster]}]}


def _price_facts(predicate_a="2026年价格", predicate_b="渠道结算价", *, unit_b=None,
                 value_b=22800, tax_b="inclusive", evidence_units=True):
    kwargs = {}
    if not evidence_units:
        kwargs = {}
    return [
        make_fact(
            "hq",
            subject="云客服专业版",
            predicate=predicate_a,
            value=23800,
            measure_unit="元/年·企业",
            currency="CNY",
            tax_basis="inclusive",
            document=PRICE_DOC_A,
            valid_from=date(2026, 1, 1),
        ),
        make_fact(
            "channel",
            subject="云客服专业版",
            predicate=predicate_b,
            value=value_b,
            measure_unit=unit_b or "元/年·企业",
            currency="CNY",
            tax_basis=tax_b,
            document=PRICE_DOC_B,
            valid_from=date(2026, 7, 1),
            valid_to=date(2026, 12, 31),
        ),
    ]


def _run(facts, docs, llm, context):
    state = build_state(facts, docs)
    resolution = AttributeResolver(llm).resolve(state)
    from truthlayer.detection.conflict import ConflictDetector

    detector = ConflictDetector(attribute_resolution=resolution)
    candidates = detector.detect(state, context)
    return candidates, detector.channel_items


# -- Golden #1: B 档只进待确认，退出码 0 -------------------------------------

def test_golden01_identity_b_measure_conflict_becomes_pending(context):
    facts = _price_facts()
    llm = FakeLLM(_merge_envelope(aliases=["2026年价格", "渠道结算价"]))
    candidates, items = _run(facts, _pricing_docs(), llm, context)

    assert candidates == []
    pending = [i for i in items if i.kind is ChannelKind.PENDING_REVIEW]
    assert len(pending) == 1
    assert pending[0].reason == "model_merged_unconfirmed"
    assert pending[0].canonical_name == "年度含税价格"


# -- Golden #11: 锚点矛盾强制拆分进跨属性摘要 --------------------------------

def test_golden11_anchor_conflict_forces_cross_attribute(context):
    facts = _price_facts(unit_b="元/人天")
    llm = FakeLLM(_merge_envelope(aliases=["2026年价格", "渠道结算价"]))
    candidates, items = _run(facts, _pricing_docs(), llm, context)

    assert candidates == []
    cross = [i for i in items if i.kind is ChannelKind.CROSS_ATTRIBUTE_REVIEW]
    assert len(cross) == 1
    assert cross[0].reason == "measure_anchor_conflict_split"
    assert [i for i in items if i.kind is ChannelKind.PENDING_REVIEW] == []


# -- Golden #12: T-02 锚点缺失降级待确认，不跳过校验 --------------------------

def test_golden12_unanchored_measure_merge_is_pending(context):
    facts = [
        make_fact(
            "a", subject="云客服专业版", predicate="报价", value=500,
            measure_unit=None, document=PRICE_DOC_A,
        ),
        make_fact(
            "b", subject="云客服专业版", predicate="渠道价", value=600,
            measure_unit=None, document=PRICE_DOC_B,
        ),
    ]
    llm = FakeLLM(_merge_envelope(canonical="报价", aliases=["报价", "渠道价"]))
    candidates, items = _run(facts, _pricing_docs(), llm, context)

    assert candidates == []
    pending = [i for i in items if i.kind is ChannelKind.PENDING_REVIEW]
    assert len(pending) == 1


# -- Golden #24: A 档×measure 锚点齐备，模型无定罪权也无翻案权 ----------------

def test_golden24_literal_measure_conflict_convicts_regardless_of_model(context):
    facts = [
        make_fact(
            "a", subject="云客服专业版", predicate="价格", value=23800,
            measure_unit="元/年·企业", currency="CNY", tax_basis="inclusive",
            document=PRICE_DOC_A, valid_from=date(2026, 1, 1),
        ),
        make_fact(
            "b", subject="云客服专业版", predicate="价格", value=22800,
            measure_unit="元/年·企业", currency="CNY", tax_basis="inclusive",
            document=PRICE_DOC_B, valid_from=date(2026, 7, 1),
            valid_to=date(2026, 12, 31),
        ),
    ]
    # Model is not even asked about same-literal predicates; empty response.
    llm = FakeLLM({"subjects": []})
    candidates, _items = _run(facts, _pricing_docs(), llm, context)

    assert len(candidates) == 1
    assert candidates[0].drift_type.value == "conflict"


def test_literal_measure_equal_value_is_silently_suppressed(context):
    facts = [
        make_fact(
            "a", subject="云客服专业版", predicate="起订量", value=5,
            measure_unit="人天", document=PRICE_DOC_A,
        ),
        make_fact(
            "b", subject="云客服专业版", predicate="起订量", value=5,
            measure_unit="人天", document=PRICE_DOC_B,
        ),
    ]
    llm = FakeLLM({"subjects": []})
    candidates, items = _run(facts, _pricing_docs(), llm, context)
    assert candidates == []
    assert items == []


def test_t02_literal_equal_number_unknown_tax_still_pending(context):
    facts = [
        make_fact(
            "a", subject="云客服专业版", predicate="价格", value=22800,
            measure_unit="元/年·企业", currency="CNY", tax_basis="inclusive",
            document=PRICE_DOC_A,
        ),
        make_fact(
            "b", subject="云客服专业版", predicate="价格", value=22800,
            measure_unit="元/年·企业", currency="CNY", tax_basis="unknown",
            document=PRICE_DOC_B,
        ),
    ]
    llm = FakeLLM({"subjects": []})
    candidates, items = _run(facts, _pricing_docs(), llm, context)
    assert candidates == []
    pending = [i for i in items if i.kind is ChannelKind.PENDING_REVIEW]
    assert len(pending) == 1
    assert pending[0].reason == "measure_anchor_missing"


# -- Golden #25: 结构疑似但未共判 → 跨属性摘要（不依赖模型） -------------------

def test_golden25_not_comerged_same_unit_goes_cross_attribute(context):
    facts = _price_facts()
    llm = FakeLLM({"subjects": []})  # model never merges them
    candidates, items = _run(facts, _pricing_docs(), llm, context)

    assert candidates == []
    cross = [i for i in items if i.kind is ChannelKind.CROSS_ATTRIBUTE_REVIEW]
    assert len(cross) == 1
    assert cross[0].reason == "same_unit_different_value_not_comerged"


def test_cross_attribute_scan_ignores_same_value_pairs(context):
    facts = _price_facts(value_b=23800)
    llm = FakeLLM({"subjects": []})
    candidates, items = _run(facts, _pricing_docs(), llm, context)
    assert candidates == []
    assert items == []


# -- 模型判等价 → normalized_equivalent 审计通道 -------------------------------

def test_model_merged_equal_measure_goes_normalized_equivalent(context):
    facts = _price_facts(value_b=23800)
    llm = FakeLLM(_merge_envelope(aliases=["2026年价格", "渠道结算价"]))
    candidates, items = _run(facts, _pricing_docs(), llm, context)

    assert candidates == []
    equivalent = [i for i in items if i.kind is ChannelKind.NORMALIZED_EQUIVALENT]
    assert len(equivalent) == 1
    assert equivalent[0].reason == "merged_measure_equal_value"


def test_model_text_equivalence_suppresses_into_channel(context):
    facts = [
        make_fact(
            "a", subject="云客服专业版", predicate="审批要求",
            value="需提前审批", object_type="string", document=PRICE_DOC_A,
        ),
        make_fact(
            "b", subject="云客服专业版", predicate="事前申请规定",
            value="事前申请", object_type="string", document=PRICE_DOC_B,
        ),
    ]
    llm = FakeLLM(
        _merge_envelope(
            canonical="事前审批要求",
            aliases=["审批要求", "事前申请规定"],
            value_kind="text",
            evidence="审批",
            equivalent_text=[["需提前审批", "事前申请"]],
        )
    )
    candidates, items = _run(facts, _pricing_docs(), llm, context)
    assert candidates == []
    equivalent = [i for i in items if i.kind is ChannelKind.NORMALIZED_EQUIVALENT]
    assert len(equivalent) == 1
    assert equivalent[0].reason == "model_text_equivalence"


# -- A × text 收紧；enumeration A 维持原行为 -----------------------------------

def test_identity_a_long_text_is_tightened_to_pending(context):
    facts = [
        make_fact(
            "a", subject="云客服专业版", predicate="续费政策",
            value="须在服务到期前至少三十个工作日完成续费审批手续",
            object_type="string",
            document=PRICE_DOC_A,
        ),
        make_fact(
            "b", subject="云客服专业版", predicate="续费政策",
            value="到期前十五天内在线提交续费申请即可自动办理完成",
            object_type="string",
            document=PRICE_DOC_B,
        ),
    ]
    llm = FakeLLM({"subjects": []})
    candidates, items = _run(facts, _pricing_docs(), llm, context)
    assert candidates == []
    pending = [i for i in items if i.kind is ChannelKind.PENDING_REVIEW]
    assert len(pending) == 1
    assert pending[0].reason == "literal_text_difference"


def test_identity_a_short_enumeration_keeps_blocking_behavior(context):
    facts = [
        make_fact(
            "a", subject="云呼叫中心", predicate="座区模式",
            value="座区制", object_type="string", document=PRICE_DOC_A,
        ),
        make_fact(
            "b", subject="云呼叫中心", predicate="座区模式",
            value="分区制", object_type="string", document=PRICE_DOC_B,
        ),
    ]
    llm = FakeLLM({"subjects": []})
    candidates, items = _run(facts, _pricing_docs(), llm, context)
    assert len(candidates) == 1
    assert candidates[0].drift_type.value == "conflict"
    assert items == []


def test_non_pricing_text_pair_keeps_legacy_blocking_behavior(context):
    facts = [
        make_fact(
            "a", subject="云客服专业版", predicate="续费政策",
            value="须在服务到期前至少三十个工作日完成续费审批手续",
            object_type="string",
            document=PRICE_DOC_A,
        ),
        make_fact(
            "b", subject="云客服专业版", predicate="续费政策",
            value="到期前十五天内在线提交续费申请即可自动办理完成",
            object_type="string",
            document=PRICE_DOC_B,
        ),
    ]
    llm = FakeLLM({"subjects": []})
    candidates, items = _run(facts, _policy_docs(), llm, context)
    assert len(candidates) == 1
    assert items == []


# -- B 档证据/锚点未验证：仍只进待确认 -----------------------------------------

def test_untrusted_b_merge_is_pending_with_other_reason(context):
    facts = _price_facts()
    llm = FakeLLM(
        _merge_envelope(
            aliases=["2026年价格", "渠道结算价"], evidence="查无此句的证据"
        )
    )
    candidates, items = _run(facts, _pricing_docs(), llm, context)
    assert candidates == []
    pending = [i for i in items if i.kind is ChannelKind.PENDING_REVIEW]
    assert len(pending) == 1
    assert pending[0].reason == "merged_pair_anchor_unverified"


# -- 无 LLM 时检测行为完全不变 -------------------------------------------------

def test_no_resolution_path_is_identical_to_legacy(context):
    facts = [
        make_fact(
            "a", subject="云客服专业版", predicate="价格", value=23800,
            document=PRICE_DOC_A, valid_from=date(2026, 1, 1),
        ),
        make_fact(
            "b", subject="云客服专业版", predicate="价格", value=22800,
            document=PRICE_DOC_B, valid_from=date(2026, 7, 1),
            valid_to=date(2026, 12, 31),
        ),
    ]
    from truthlayer.detection.conflict import ConflictDetector

    state = build_state(facts, _pricing_docs())
    detector = ConflictDetector()
    candidates = detector.detect(state, context)
    assert len(candidates) == 1
    assert candidates[0].drift_type.value == "conflict"
