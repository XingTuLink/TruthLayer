"""Versioned extraction prompts.

The prompt version is recorded on every ScanRun so extractions are
reproducible/auditable (#15). Changing the prompt MUST bump PROMPT_VERSION.
"""

from __future__ import annotations

PROMPT_VERSION = "fact-extract-v1"

SYSTEM_PROMPT = """\
你是企业知识事实抽取器。你的输出只是候选，系统会做确定性校验。

规则：
1. 只抽取文档中明确陈述的事实，禁止推测、补全或使用常识。
2. 每个事实必须包含主语实体、谓词、唯一宾语，以及原文中逐字出现的证据引用（quote）。
3. 宾语二选一：另一个实体（object_entity，给出其实体名）或标量值
   （object_value + object_type，类型为 string/number/date/boolean 之一）。
4. 数字用 JSON number；日期用 YYYY-MM-DD 字符串；布尔用 true/false。
   价格/金额只抽取数值，单位（如"元/月"）放进谓词或字符串宾语中。
5. 事实中出现的每个实体都必须列入 entities；实体 type 用简短类别
   （如 product / customer / policy / person / org）。
6. quote 必须是输入文本中连续的逐字片段（允许空白差异），不得改写。
7. 生效/失效日期填入 valid_from / valid_to；文档中观察到该事实的日期
   填入 observed_at；都没有就给 null。
8. 不确定或表述含糊的内容不要抽取。confidence 取 0 到 1。
9. 没有可抽取内容时返回空数组。
"""


def build_user_prompt(
    *,
    chunk_text: str,
    filename: str,
    page: int | None = None,
) -> str:
    """Wrap one chunk with its provenance hint."""
    location = f"来源文件：{filename}"
    if page is not None:
        location += f"，第 {page} 页"
    return f"{location}\n\n文档片段：\n{chunk_text}"
