"""Versioned extraction prompts.

The prompt version is recorded on every ScanRun so extractions are
reproducible/auditable (#15). Changing the prompt MUST bump PROMPT_VERSION.
"""

from __future__ import annotations

PROMPT_VERSION = "fact-extract-v3"

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
7. 日期口径（务必严格，错误的日期会被下游当成确定性失效信号）：
   - valid_from：仅当原文明确写出生效或更新起始日时填写
     （如“自 2026-01-01 起执行”“更新日期为 2026-06-01”）。
   - valid_to：仅在两种情况下填写——(a) 原文明确给出失效/截止/到期/废止日
     （如“有效期至 2026-06-30”“自 X 起废止”）；(b) 原文明确限定了一个适用期间，
     且其结束日可由日历确定性推出（如“适用于 2026 年第二季度”→ 2026-06-30，
     “适用于 2026 年”→ 2026-12-31）。
   - 循环性、季节性或“长期有效/未废止”的表述绝不填写 valid_to：
     如“每年 6 至 8 月发放”“每季度复核”“长期执行”。这类内容会重复发生，
     并不会在某个具体日期失效。
   - 严禁根据复核日、观察日、季节窗口、发布日或常识推测 valid_to。
   - observed_at：原文中“观察 / 最近一次复核 / 确认该事实仍然有效”的日期。
     这类日期是事实的元数据，必须填入相关事实的 observed_at，而不要只抽成
     一个谓词。例：“最近一次复核日期为 2025-07-15，确认继续执行”
     → 该条事实 observed_at=2025-07-15、valid_to=null；
     “更新日期为 2026-06-01” → observed_at=2026-06-01。
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
