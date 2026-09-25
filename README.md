# TruthLayer

> 🌐 语言：**简体中文** · [English](./README.en.md)

**持续检查 AI 所依赖的企业知识 —— CLI-first 的 Knowledge Drift Detector。**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-green)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-153%20passed-success)](#测试)

企业制度、价格表、产品手册会持续修订，但 AI 助手引用的往往是旧版本：跨文档口径
冲突、已被新版本取代的政策、无人复核的过期数字、同一实体的多种异写……TruthLayer
把散落在文档里的知识抽取为带**原文证据**的结构化事实，并在每次扫描时确定性地检测
这些"知识漂移"，在错误答案到达用户之前给出可追溯的告警。

- **CLI-first**：一条 `truthlayer scan` 跑完整条链路，可直接接入 CI（零服务依赖）
- **Evidence First**：每条事实必须锚定文档原文引用，无证据不落库
- **LLM 只做候选，规则才裁决**：大模型负责抽取，冲突/过期判定全部由确定性规则完成
- **Embedding 只召回不裁决**：向量用于候选召回，最终结论不依赖向量相似度
- **本地可跑**：OpenAI 兼容协议，云端模型网关与本机 [Ollama](https://ollama.com/) 通用，支持零 API Key

---

## 目录

- [它能检测什么](#它能检测什么)
- [工作流程](#工作流程)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [CLI 命令](#cli-命令)
- [测试](#测试)
- [文档](#文档)
- [路线图](#路线图)
- [参与贡献](#参与贡献)
- [许可证](#许可证)

---

## 它能检测什么

| 漂移类型 | 含义 | 典型场景 |
|---|---|---|
| `conflict` | 同一主体、同一谓词出现互相矛盾的有效值 | A 文档写座区制、B 文档写分区制 |
| `confirmed_stale` | 事实已过期，或来源文档已被显式取代 | 2026 价格表声明取代 2025 版 |
| `possibly_stale` | 事实长期未被复核（仅有年龄信号，警告级） | 定价类事实超过 90 天未见更新 |
| `superseded` | 文档存在明确的新版本 | `产品价格表_2025.csv → _2026.csv` |
| `duplicate` | 两个实体名称异写且共享鉴别性事实，疑似未归并 | "ACME CRM" 与 "ACME CRM Pro" |

每条检测结果都带有严重级别（warning / medium / high / critical）、置信度、
AI 影响等级，以及指向**新旧事实、来源文档、原文证据**的完整链接。

已报告过的问题通过确定性指纹跨扫描去重——不会每次扫描都重复打扰。

## 工作流程

```text
 企业文档 (TXT/MD/CSV/PDF/DOCX/XLSX)
        │
        ▼
 发现 → 解析 → 规范化 → 切块
        │
        ▼
 LLM 候选抽取（实体 / 事实 / 关系）
        │  结构化 schema + 确定性校验
        ▼
 Entity / Fact / Evidence 落库（每条事实必须挂原文证据）
        │
        ├──▶ chunk / entity 向量化（pgvector，维度自由）
        ├──▶ 不可变快照 + 确定性 Knowledge Hash
        │
        ▼
 四个确定性检测器 → 五类漂移 → 持久化 / 去重 / 摘要输出
```

## 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL 15+ 且安装 [pgvector](https://github.com/pgvector/pgvector) 扩展
- （可选）本机 [Ollama](https://ollama.com/) 拉取 `qwen2.5` 与 `bge-m3`，即可零 Key 完整运行；
  也可使用任意 OpenAI 兼容的云端端点

### 1. 安装

```powershell
git clone git@atomgit.com:XingTuLink/TruthLayer.git
cd TruthLayer

python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,parsers,openai]"
```

### 2. 初始化数据库

```powershell
$env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://用户:密码@localhost:5432/truthlayer"

.\.venv\Scripts\python scripts/dev_init_db.py   # 幂等建库
.\.venv\Scripts\alembic upgrade head            # 建表 + pgvector 扩展
```

### 3. 跑一遍内置演示知识库

仓库自带一家虚构公司 "ACME" 的知识库：休假/差旅制度两份、跨年价格表两份、
以及一份应被忽略的内部草稿。配置已指向本机 Ollama（无需 API Key）：

```powershell
# 仅做文档接入与配置校验
.\.venv\Scripts\truthlayer check .\examples\demo_kb

# 完整扫描：抽取事实 → 向量 → 快照 → 漂移检测
.\.venv\Scripts\truthlayer scan .\examples\demo_kb
```

扫描输出包含抽取统计、确定性知识哈希与漂移检测摘要。再次扫描内容不变时，
哈希保持一致、漂移全部显示为 already known（幂等）。

## 配置说明

在知识库根目录放置 `.truthlayer.yaml`（完整示例见
[examples/demo_kb/.truthlayer.yaml](./examples/demo_kb/.truthlayer.yaml)）：

```yaml
workspace:
  name: acme-demo

sources:                        # 知识来源目录 + 权威度 + 来源类型
  - path: ./docs/policies
    authority: 0.95
    type: policy
  - path: ./docs/pricing
    authority: 0.9
    type: pricing

rules:
  stale_after_days: 365         # 一般事实未复核告警阈值
  pricing_stale_days: 90        # 定价类事实更短的阈值

severity:                       # 按来源类型覆盖冲突严重级
  pricing_change: high
  policy_change: critical

ci:
  fail_on: critical             # CI 失败阈值（Sprint 5 启用退出码）

version_mapping:                # 显式版本链：只承认声明，绝不按文件名猜
  - group: product_pricing
    files:
      - path: 产品价格表_2025.csv
        label: "2025"
      - path: 产品价格表_2026.csv
        label: "2026"
        supersedes: 产品价格表_2025.csv

ignore:
  - "**/internal-test/**"

extraction:                     # OpenAI 兼容端点（云端 / Ollama 通用）
  provider: openai_compatible
  model: qwen2.5:7b
  base_url: http://localhost:11434/v1

embedding:
  provider: openai_compatible
  model: bge-m3
  base_url: http://localhost:11434/v1
  dimensions: 1024
```

**凭据规则**：API Key 只从环境变量读取（`TRUTHLAYER_LLM_API_KEY` /
`TRUTHLAYER_EMBEDDING_API_KEY`，可用 `api_key_env` 改名），永远不写进配置文件；
本机回环端点无需 Key。

## CLI 命令

| 命令 | 说明 |
|---|---|
| `truthlayer check <path>` | 校验配置并执行文档接入（解析/切块/版本链） |
| `truthlayer scan <path>` | 完整扫描：接入 + 抽取 + 向量 + 快照 + 漂移检测 |
| `truthlayer --version` | 版本信息 |

> 漂移处置（`drift list/show`、`resolve`）、CI fail_on 退出码与
> HTML/JSON 报告正在开发中，见[路线图](#路线图)。

退出码：`0` 正常；`2` 配置/数据库/解析等系统错误。

## 测试

```powershell
# 单元测试（无需数据库，确定性、毫秒级）
.\.venv\Scripts\python -m pytest tests/unit

# 全量测试（含 PostgreSQL 集成测试：自动使用一次性隔离库 <dbname>_it，
# 会话结束自动清理，不污染开发库）
$env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://..."
.\.venv\Scripts\python -m pytest
```

当前测试套件 **153 个测试全部通过**：单元测试覆盖规范化/哈希/实体解析/证据校验/
四个检测器的全部判定规则与负例；集成测试在真实 PostgreSQL 上覆盖五类型漂移的
检出、落库字段与跨扫描指纹去重。另有 1 个 Ollama 真实冒烟测试默认跳过
（设置 `TRUTHLAYER_RUN_OLLAMA=1` 才运行）。

## 文档

- 架构说明：[简体中文](./docs/architecture.md) · [English](./docs/architecture.en.md)
  —— 分层设计、数据模型、检测原理与关键设计决策
- 贡献指南：[简体中文](./CONTRIBUTING.md) · [English](./CONTRIBUTING.en.md)
  —— 开发环境、编码纪律、测试要求与 PR 流程

## 路线图

TruthLayer 当前处于 **Phase 0（CLI 版本）**，按 6 个 Sprint 迭代：

- [x] Sprint 1–2：项目骨架、领域模型、6 种文档格式接入、版本链
- [x] Sprint 3：LLM 知识抽取、Evidence First、向量与不可变快照
- [x] Sprint 4：漂移检测引擎（四检测器 / 五类型、指纹去重）
- [ ] Sprint 5：漂移处置流程、`drift`/`resolve` CLI、CI fail_on、HTML/JSON 报告
- [ ] Sprint 6：评测集与指标验收（Precision / Recall / FPR）

**Phase 0 Gate 验收通过后正式发布 v0.1。** 当前版本号为 `0.0.1`（开发中）。

## 参与贡献

欢迎 Issue 与 PR！提交前请阅读[贡献指南](./CONTRIBUTING.md)：开发环境搭建、
不可违反的设计红线（Evidence First、LLM 只做候选、Embedding 不裁决等）、
测试要求与提交规范。

## 许可证

本项目基于 GNU Affero General Public License v3.0（AGPL-3.0） 开源，© 西安栈上月明软件科技有限公司。

请注意 AGPL 的关键约束：如果你修改了本项目并通过网络向用户提供服务（包括 SaaS / 托管形式），必须以相同协议向网络使用者公开修改后的完整源代码。

官方代码仓库：

- AtomGit：`git@atomgit.com:XingTuLink/TruthLayer.git`
- GitHub：`git@github.com:XingTuLink/TruthLayer.git`
- Gitee：`https://gitee.com/XingTuLink/truth-layer`
