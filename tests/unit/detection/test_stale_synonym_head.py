"""Chain-head synonym restatement gate for StaleDetector (design 04 §11).

A superseded document flags every one of its facts. When the chain head
restates the same clause under a *different* predicate wording with an
equivalent value, nothing drifted — only the edition did. The gate is narrow:
trusted non-measure attributes only, literal-different predicates only, so the
frozen measure semantics (R16-17: identical value across editions still
reports) stay intact.
"""

from __future__ import annotations

from truthlayer.attributes import AttributeResolver
from truthlayer.attributes.schemas import AttributeResolutionEnvelope

from .conftest import build_state, make_document, make_fact

DOC_OLD = "员工手册_2023.md"
DOC_HEAD = "员工手册_2025.md"


class FakeLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    def generate_structured(
        self, *, input_text, output_schema, system_prompt=None, model=None
    ):
        return AttributeResolutionEnvelope.model_validate(self._payload)


def _docs():
    return [
        make_document(DOC_OLD, group="handbook", source_type="pricing"),
        make_document(
            DOC_HEAD, group="handbook", supersedes=DOC_OLD, source_type="pricing"
        ),
    ]


def _envelope(
    canonical="事假薪酬口径",
    *,
    aliases,
    value_kind="text",
    evidence="事假",
    equivalent_text=None,
    subject="云启信息",
):
    cluster = {
        "canonical_name": canonical,
        "definition": "事假期间的薪酬待遇口径",
        "value_kind": value_kind,
        "aliases": [
            {"predicate": alias, "evidence_span": evidence, "confidence": 0.9}
            for alias in aliases
        ],
        "equivalent_text_values": equivalent_text or [],
    }
    return {"subjects": [{"subject": subject, "clusters": [cluster]}]}


def _facts(
    old_predicate="事假工资",
    old_value="无薪",
    head_predicate="事假是否带薪",
    head_value="无薪",
    *,
    old_type="string",
    head_type="string",
    old_unit=None,
    head_unit=None,
):
    return [
        make_fact(
            "old",
            subject="云启信息",
            predicate=old_predicate,
            value=old_value,
            object_type=old_type,
            measure_unit=old_unit,
            document=DOC_OLD,
        ),
        make_fact(
            "head",
            subject="云启信息",
            predicate=head_predicate,
            value=head_value,
            object_type=head_type,
            measure_unit=head_unit,
            document=DOC_HEAD,
        ),
    ]


def _run(facts, llm, context):
    state = build_state(facts, _docs())
    resolution = AttributeResolver(llm).resolve(state)
    from truthlayer.detection.stale import StaleDetector

    detector = StaleDetector(attribute_resolution=resolution)
    return detector.detect(state, context)


def _confirmed(candidates):
    return [c for c in candidates if c.drift_type.value == "confirmed_stale"]


# -- Gate fires: different wording, equivalent value -------------------------

def test_different_wording_same_value_is_silent(context):
    facts = _facts(head_value="无薪")
    llm = FakeLLM(_envelope(aliases=["事假工资", "事假是否带薪"]))
    candidates = _run(facts, llm, context)
    assert _confirmed(candidates) == []


def test_different_wording_model_equivalent_value_is_silent(context):
    facts = _facts(head_value="不予发放")
    llm = FakeLLM(
        _envelope(
            aliases=["事假工资", "事假是否带薪"],
            equivalent_text=[["无薪", "不予发放"]],
        )
    )
    candidates = _run(facts, llm, context)
    assert _confirmed(candidates) == []


# -- Gate must not fire -----------------------------------------------------

def test_different_wording_different_value_still_reports(context):
    facts = _facts(head_value="带薪")
    llm = FakeLLM(_envelope(aliases=["事假工资", "事假是否带薪"]))
    candidates = _run(facts, llm, context)
    confirmed = _confirmed(candidates)
    assert len(confirmed) == 1
    assert confirmed[0].detail["reason"] == "superseding_source"


def test_same_predicate_literal_same_value_still_reports(context):
    """R16-17 semantics: literal-identical restatement is never suppressed."""
    facts = _facts(head_predicate="事假工资", head_value="无薪")
    llm = FakeLLM({"subjects": []})
    candidates = _run(facts, llm, context)
    assert len(_confirmed(candidates)) == 1


def test_untrusted_binding_still_reports(context):
    facts = _facts(head_value="无薪")
    llm = FakeLLM(
        _envelope(aliases=["事假工资", "事假是否带薪"], evidence="查无此句的证据")
    )
    candidates = _run(facts, llm, context)
    assert len(_confirmed(candidates)) == 1


def test_measure_attribute_is_out_of_scope(context):
    facts = _facts(
        old_predicate="单价",
        old_value=22800,
        head_predicate="结算价",
        head_value=22800,
        old_type="number",
        head_type="number",
        old_unit="元/年·企业",
        head_unit="元/年·企业",
    )
    llm = FakeLLM(
        _envelope(
            canonical="年度含税价格",
            aliases=["单价", "结算价"],
            value_kind="measure",
            evidence="价",
        )
    )
    candidates = _run(facts, llm, context)
    assert len(_confirmed(candidates)) == 1


def test_numeric_coincidence_still_reports_even_if_model_says_text(context):
    """Numeric clauses that merely share a value must never be read as a
    restatement, whatever value_kind the model assigned."""
    facts = _facts(
        old_predicate="试用期",
        old_value=3,
        head_predicate="迟到早退免罚累计次数",
        head_value=3,
        old_type="number",
        head_type="number",
    )
    llm = FakeLLM(
        _envelope(
            canonical="数字口径",
            aliases=["试用期", "迟到早退免罚累计次数"],
            value_kind="text",
            evidence="试用",
        )
    )
    candidates = _run(facts, llm, context)
    assert len(_confirmed(candidates)) == 1


def test_no_resolution_keeps_legacy_behavior(context):
    facts = _facts(head_value="无薪")
    state = build_state(facts, _docs())
    from truthlayer.detection.stale import StaleDetector

    detector = StaleDetector()
    candidates = detector.detect(state, context)
    assert len(_confirmed(candidates)) == 1