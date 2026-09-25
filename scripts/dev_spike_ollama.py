"""One-off spike: verify Ollama's OpenAI-compatible endpoints.

Checks: server reachability, embedding dimensionality (bge-m3), and
structured-output compliance of qwen2.5:7b via response_format json_schema.
No credentials needed; Ollama ignores the api key.
"""

from __future__ import annotations

import json

from openai import OpenAI

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

print("== models ==")
for m in client.models.list().data:
    print(" -", m.id)

print("\n== embedding bge-m3 ==")
emb = client.embeddings.create(
    model="bge-m3", input=["ACME CRM Pro 版本价格为 149 元每月"]
)
vec = emb.data[0].embedding
print("dimensions:", len(vec), "| first 5:", [round(x, 4) for x in vec[:5]])

print("\n== structured output via /v1/chat/completions ==")
schema = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {"type": "string"},
                    "valid_from": {"type": ["string", "null"]},
                },
                "required": ["subject", "predicate", "value"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}

doc = (
    "产品价格表（2026年1月1日生效）：ACME CRM Pro 每用户每月 149 元；"
    "ACME CRM Enterprise 每用户每月 329 元。"
)
resp = client.chat.completions.create(
    model="qwen2.5:7b",
    temperature=0,
    messages=[
        {
            "role": "system",
            "content": (
                "你是企业知识事实抽取器。只抽取文档中明确陈述的事实，"
                "不做推测，严格按给定 JSON schema 输出。"
            ),
        },
        {"role": "user", "content": doc},
    ],
    response_format={
        "type": "json_schema",
        "json_schema": {"name": "fact_extraction", "schema": schema},
    },
)
content = resp.choices[0].message.content
parsed = json.loads(content)
print(json.dumps(parsed, ensure_ascii=False, indent=2))
print("usage:", resp.usage)
