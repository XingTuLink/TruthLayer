"""Immutable edition-metadata never raises stale drifts (run12 HCR fix).

run12 HCR adjudication found 38/79 false confirmed/possibly-stale alerts
were document-identity metadata (文件编号/版本/生效日期/制单部门/产品编码…):
statements that stay true about their own edition forever. Both stale paths
must stay silent for predicates listed in
``rules.immutable_metadata_predicates``.

Exception (drift-core-v9): an expired evidence-anchored validity-END
statement (有效期至 / 失效日期 …) on a standalone document is the document's
own declaration of invalidity — it still never raises a FACT-level row, but
it now produces exactly one target=document confirmed_stale card.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from truthlayer.detection.stale import StaleDetector
from truthlayer.domain.enums import DriftType

from .conftest import build_state, make_document, make_fact


def test_metadata_on_superseded_edition_not_confirmed_stale(context):
    state = build_state(
        [
            make_fact(
                "code",
                subject="云客服专业版",
                predicate="产品编码",
                value="YKF-PRO-2024",
                object_type="string",
                document="price_2025.csv",
            )
        ],
        documents=[
            make_document("price_2025.csv", source_type="pricing", group="p"),
            make_document(
                "price_2026.csv",
                source_type="pricing",
                group="p",
                supersedes="price_2025.csv",
            ),
        ],
    )

    assert StaleDetector().detect(state, context) == []


def test_expired_validity_statement_emits_document_card_not_fact_card(context):
    from truthlayer.domain.enums import TargetType

    state = build_state(
        [
            make_fact(
                "eff",
                subject="差旅制度（2022版）",
                predicate="有效期至",
                value="2023-12-31",
                object_type="string",
                document="travel_2022.md",
                valid_to=date(2023, 12, 31),
            )
        ],
        documents=[make_document("travel_2022.md", source_type="policy")],
    )

    results = StaleDetector().detect(state, context)

    # No fact-level stale row for the metadata statement itself...
    assert [c for c in results if c.target_type == TargetType.FACT] == []
    # ...but one target=document card (legacy None anchor stays trusted).
    doc_rows = [c for c in results if c.target_type == TargetType.DOCUMENT]
    assert len(doc_rows) == 1
    card = doc_rows[0]
    assert card.drift_type is DriftType.CONFIRMED_STALE
    assert card.detail["reason"] == "document_self_declared_expired"
    assert card.old_fact_id is not None


def test_metadata_age_over_threshold_not_possibly_stale(context):
    state = build_state(
        [
            make_fact(
                "no",
                subject="服务SLA",
                predicate="文件编号",
                value="SLA-2021-001",
                object_type="string",
                document="sla_2021.md",
                observed_at=date(2021, 3, 1),
            )
        ],
        documents=[make_document("sla_2021.md")],
    )

    assert StaleDetector().detect(state, context) == []


def test_business_fact_on_superseded_edition_still_reports(context):
    state = build_state(
        [make_fact("price", predicate="list_price", document="price_2025.csv")],
        documents=[
            make_document("price_2025.csv", source_type="pricing", group="p"),
            make_document(
                "price_2026.csv",
                source_type="pricing",
                group="p",
                supersedes="price_2025.csv",
            ),
        ],
    )

    results = StaleDetector().detect(state, context)
    assert len(results) == 1
    assert results[0].drift_type is DriftType.CONFIRMED_STALE


def test_empty_predicate_config_reopens_metadata_alerts(context):
    state = build_state(
        [
            make_fact(
                "no",
                subject="服务SLA",
                predicate="文件编号",
                value="SLA-2021-001",
                object_type="string",
                document="sla_2021.md",
                observed_at=date(2021, 3, 1),
            )
        ],
        documents=[make_document("sla_2021.md")],
    )
    ctx = replace(context, immutable_metadata_predicates=frozenset())

    results = StaleDetector().detect(state, ctx)
    assert len(results) == 1
    assert results[0].drift_type is DriftType.POSSIBLY_STALE


def test_metadata_match_strips_whitespace(context):
    state = build_state(
        [
            make_fact(
                "ver",
                subject="产品服务报价单（2024版）",
                predicate=" 版本 ",
                value="V1.0",
                object_type="string",
                document="price_2024.csv",
                observed_at=date(2024, 1, 1),
            )
        ],
        documents=[make_document("price_2024.csv")],
    )

    assert StaleDetector().detect(state, context) == []
