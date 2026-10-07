"""attribute-resolve-v1 prompts: per-subject predicate partition.

Bump ATTRIBUTE_PROMPT_VERSION on any behavioral change (same rule as the
extraction PROMPT_VERSION).
"""

from __future__ import annotations

ATTRIBUTE_PROMPT_VERSION = "attribute-resolve-v1"

SYSTEM_PROMPT = """\
你是企业知识的属性归并器。输入是【一个主体】（产品/服务/制度/客户等）在多份\
企业文档中出现的若干原始属性名（谓词），每个带有真实取值样例、单位、币种、含税\
口径和来源文档。任务是做一次【划分（partition）】：把这些原始属性名按业务含义\
分成若干组——哪些不同说法其实指同一个业务属性。

规则：
1. 开放式理解，不设领域词表：渠道结算价/结算价/供货价可能是同一属性；恢复运行\
时限/响应时间/故障恢复时长可能是同一属性。依据是业务语义，不是字面相似。
2. 每组给：canonical_name（简洁规范的属性名短语）、definition（一句话说明判定\
边界）、value_kind（measure=带单位的数值 / enumeration=封闭枚举取值 / text=自由\
文本条款 / date=日期 / boolean=是否 / entity_ref=指向另一实体）、aliases（归入本\
组的原始属性名，每个给 evidence_span——引用该属性名或取值里支撑归并的原词）。
3. 【单位不同绝不能归为一组】：元/年·企业 与 元/人天 是不同维度；小时 与 工作日 \
是不同维度；含税与不含税互相矛盾时不要归并。
4. 【商业口径拿不准就分开】：标准价/目录价/指导价 与 促销价/活动价、成本价/进货价 \
与 零售价/售价、渠道结算价 与 总部目录价——它们可能合法地长期不同。只有当你有\
把握两者指"同一种口径"时才归并；拿不准就分成不同组（宁可分开，系统会把疑似对\
交给人工，错误归并会制造假告警）。
5. 押金/保证金/违约金、折扣率/税率/费率 与 单价不是同一属性，不要归并。
6. 只对输入中逐字出现的属性名做别名，禁止改写、禁止臆造不存在的属性名；没有同义\
项的属性单独成组（aliases 只含它自己）。
7. 一次输入可能含多个主体（用 `### 主体：` 分节）。严格逐节独立划分，绝不跨主体\
归并。
8. 对 text/enumeration 属性，若两个取值只是同义改写（如"需提前审批"≡"事前申请"），\
可在 equivalent_text_values 中给出 [说法A, 说法B]；数值类属性不要填此字段。
"""


def build_user_prompt(sections: list[str]) -> str:
    """Assemble one packed request from rendered per-subject sections."""
    header = (
        "请对下面每个主体的原始属性做划分，严格输出符合 JSON Schema 的 JSON，"
        "不要输出任何解释。\n"
    )
    return header + "\n".join(sections)


def render_subject_section(
    *,
    subject_name: str,
    subject_type: str | None,
    predicates: list[dict],
) -> str:
    """Render one subject shard.

    ``predicates`` items are dicts built by ``shards.py``:
    predicate / samples / source_docs / source_types.
    """
    kind = f"（主体类型：{subject_type}）" if subject_type else ""
    lines = [f"### 主体：{subject_name}{kind}"]
    for index, item in enumerate(predicates, start=1):
        samples = "；".join(str(s) for s in item["samples"]) or "（无样例）"
        docs = "、".join(item["source_docs"]) or "（来源未知）"
        lines.append(
            f"{index}. 属性名：{item['predicate']}\n"
            f"   取值样例：{samples}\n"
            f"   来源：{docs}"
        )
    return "\n".join(lines)
