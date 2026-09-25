# 架构说明

> 🌐 语言：**简体中文** · [English](./architecture.en.md)

本文面向希望理解系统设计或参与开发的贡献者，介绍 TruthLayer 的分层结构、
数据模型、处理流程与关键设计决策。

## 1. 总览

TruthLayer 是一个 CLI-first 的本地/CI 工具：一次扫描把企业文档变成带证据的
结构化知识，并在当前知识状态上运行确定性的漂移检测器。整个过程不需要常驻服务，
事务边界由 CLI 控制；同一套 service 层未来可直接被 API 复用。

```text
┌─────────────────────────────────────────────────────────────┐
│  CLI（Typer 薄壳，只负责参数/事务/输出，不写业务逻辑）          │
├─────────────────────────────────────────────────────────────┤
│  ingestion service → extraction service → detection service │
├──────────┬──────────────────┬──────────────────┬────────────┤
│ ingestion│   extraction     │    detection     │ providers  │
│ 解析/切块 │  LLM 抽取/实体归一 │  状态视图/检测器   │ OpenAI 兼容 │
│ 版本链    │  证据/哈希/快照    │  指纹/编排落库     │ LLM/Embed  │
├──────────┴──────────────────┴──────────────────┴────────────┤
│  domain：枚举 / FactClaim / Evidence（纯规则，零基础设施依赖）  │
├─────────────────────────────────────────────────────────────┤
│  SQLAlchemy 2 ORM + Alembic  ·  PostgreSQL 15 + pgvector     │
└─────────────────────────────────────────────────────────────┘
```

## 2. 分层职责

| 包 | 职责 | 依赖约束 |
|---|---|---|
| `truthlayer.domain` | 枚举、错误类型、`FactClaim`/`Evidence` 值对象与全部不变量校验 | 不依赖 ORM、CLI、厂商 SDK |
| `truthlayer.db` | SQLAlchemy ORM（13 张表）、session、Alembic 迁移 | 只被 service 层调用 |
| `truthlayer.ingestion` | 文件发现、6 种格式解析、编码兜底、规范化、哈希、切块、显式版本链 | 不认识 LLM |
| `truthlayer.extraction` | 抽取 schema/prompt、实体解析、事实与证据落库、知识哈希、不可变快照 | 通过 providers 抽象调用模型 |
| `truthlayer.detection` | 知识状态加载、候选/指纹、四个检测器、检测编排服务 | 检测器不接触 session/ORM |
| `truthlayer.providers` | OpenAI 兼容的 LLM/Embedder 实现（云端、网关、Ollama 通用） | 协议化，可替换 |
| `truthlayer.cli` | Typer 命令、事务提交、终端输出 | 薄壳，禁止写业务规则 |

### 两条事务纪律

1. **service 层只 `flush` 不 `commit`**：一次 scan 是 ingestion → extraction →
   detection 串起来的单一事务，由 CLI 在全部成功后统一提交，失败整体回滚。
2. **检测器只读不可变视图**：`KnowledgeState`（frozen dataclass）在检测开始前
   一次性从 ORM 加载，检测器无法写库、也无法依赖查询副作用，保证结果可重放。

## 3. 数据模型

核心 13 张表（见迁移 `alembic/versions/0001_initial.py`）：

```text
workspaces
├── document_groups ── documents ── chunks
│     (版本组)           │  ↑ previous_version_id（显式取代链）
│                        ├── facts ── (fact_evidence 通过证据字段内联)
│                        └── entities ── entity_aliases
├── scan_runs           （每次扫描的完整留痕，含 detector_version）
├── drifts              （target 多态：可指向 fact 或 document，刻意无 FK）
├── resolutions         （Sprint 5）
└── snapshots + snapshot_facts / snapshot_entities（不可变冻结）
```

数据库层强制的关键约束：

- **事实宾语 XOR**：`object_entity_id` 与 `object_value` 必须且只能存在一个（CHECK）；
- **时间窗**：`valid_from <= valid_to`（CHECK）；
- confidence / authority_score 限定 0–1；
- 文件级幂等：`UNIQUE(workspace_id, file_hash)`；
- Drift 的 `target_id` 刻意不建外键（多态引用 fact/document，由 service 校验）。

## 4. 扫描流水线

### 4.1 接入（ingestion）

发现（确定性排序、ignore glob、重叠 source 去重）→ 原始字节 SHA-256
（`file_hash`，文件级幂等）→ 解析（TXT/MD/CSV/PDF/DOCX/XLSX，失败只标记不中断）
→ 规范化（NFC、CRLF、保守去空白）→ canonical JSON 哈希（`content_hash`）
→ 约 512 token 贪心切块（超长滑窗、`chunk_index` 对内容稳定）。

**版本链只承认显式声明**：配置里 `supersedes` 了才链接两个文档，绝不通过
版本标签字符串排序推导（`v10` 不会因此挂到 `v2`）；引用缺失/歧义只告警。

### 4.2 抽取（extraction）

- LLM 输出受版本化 schema（`fact-extract-v1`）约束，支持 strict json_schema →
  JSON mode → prompt-only 三级降级；**任何一级的输出都要再过我方 Pydantic +
  `FactClaim`/`Evidence` 确定性校验**；
- 实体解析顺序：规范名精确匹配 → 别名 → 全局唯一匹配；跨类型歧义直接拒绝、
  不猜测；小模型漏报实体时，未声明引用确定性补救为 `unknown` 类型并告警
  （证据要求与歧义拒绝两条红线不放松）；
- **Evidence First**：每条事实至少 1 条证据（文档、chunk、页码、逐字引文、
  来源类型、权威度）；模型引文不在原文中时降级锚定到 chunk 原文并告警；
- 扫描结束写不可变快照（只追加），并计算**确定性 Knowledge Hash**：每条事实
  投影为排序键的 canonical JSON（排除 id/时间戳/向量/置信度），整体排序后
  SHA-256 —— 与插入顺序无关，内容不变哈希不变。

### 4.3 检测（detection）

`KnowledgeState` 只加载 active facts（JSONB 标量的 date 会从 ISO 还原），
四个检测器各自输出 `DriftCandidate`：

| 检测器 | 判定要点 |
|---|---|
| Conflict | 同主体同谓词分组；同值（多证据 Canonical Fact）、时间窗不重叠、同文档（tier 表）、有显式新版本、配置为多值谓词——全部跳过 |
| Stale | `valid_to` 过期或存在取代源 → **confirmed**（0.95）；仅年龄超阈值（pricing 90 天/其他 365 天）→ **possibly**（0.6）；**无年龄信号永不判** |
| Superseded | document 级，只沿显式 `previous_version_id` 链，n 版产 n−1 条 |
| Duplicate | 名称/向量只做**召回**；确认要求同谓词+时间窗重叠+宾语规范相等；单条共同事实还须具备鉴别力（该值全库仅两个实体持有），通用属性（如计价单位）不构成归并证据 |

置信度档位：结构证据 0.95 / 召回类 0.90 / 年龄启发式 0.6。
severity 与 ai_impact_level 是两个独立概念，分开存储。

编排服务为每个候选计算**确定性指纹**（类型+谓词+新旧事实 id 等的 SHA-256），
与历史所有漂移（含 ignored/resolved）的指纹比对去重，新发现才落库；
`detector_version`（当前 `drift-core-v1`）写入 scan_run，检测规则演进后可追溯。

## 5. 配置与凭据

- `.truthlayer.yaml` 全部字段由 Pydantic 强校验（`extra="forbid"`，未知键即报错），
  包括 sources 权威度、过期阈值、severity 覆盖、CI fail_on、显式版本链、
  extraction/embedding 双端点槽位；
- **凭据只走环境变量**：配置里只允许出现 `api_key_env` 的变量名；
- LLM 与 embedding 可以是同一供应商（一个 Key）、两家供应商（两个 Key）
  或本机 Ollama（零 Key）；embedding 槽位整体可选，缺向量不阻塞知识落库；
  向量列不锁定维度，实际维度记入 scan_run。

## 6. 错误处理

领域层定义 8 类带语义的错误（配置/解析/Provider/数据库/领域校验等）。
CLI 退出码：`0` 成功；`2` 系统错误（配置非法、数据库不可用、存在解析失败文件）；
fail_on 阈值失败码 `1` 将在 Sprint 5 启用。失败的 scan 也会在 `scan_runs`
留痕（status=failed、error_message）。

## 7. 测试架构

- **单元测试**（`tests/unit/`，无需数据库）：纯函数与检测器为主。检测器测试使用
  内存中的 frozen 状态工厂，刻意覆盖负例（同文档 tier、149==149.0、时间窗不重叠、
  通用属性误归并等）；
- **集成测试**（`tests/integration/`）：每个会话使用一次性隔离库 `<dbname>_it`，
  自动 `alembic upgrade head` / 结束 `downgrade base`，直接构造 ORM 行而不调用 LLM，
  覆盖五类型漂移的检出、字段与跨扫描去重；
- **Ollama 冒烟测试**：默认 skip，`TRUTHLAYER_RUN_OLLAMA=1` 时对真实模型端到端验证，
  CI 无 Ollama 不受影响。

## 8. 关键设计决策（贡献者必读）

1. **Evidence First**：没有逐字可溯源证据的事实不允许存在；
2. **LLM 只做候选**：模型输出永远是"候选"，准入由确定性规则裁决；
3. **Embedding 只召回不裁决**：任何漂移结论不得仅由向量相似度得出；
4. **确定性优先**：知识哈希、检测指纹、切块编号、文件排序全部可复现；
5. **显式优于猜测**：版本链只认配置声明，歧义默认拒绝并告警；
6. **CLI 是薄壳**：业务逻辑只允许出现在 domain/service 层。
