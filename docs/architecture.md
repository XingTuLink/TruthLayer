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
│ ingestion → extraction → detection → resolution → reporting │
├──────────┬────────────┬──────────┬────────────┬────────────┤
│ ingestion│ extraction │detection │ resolution │ reporting  │
│ 解析/切块 │ LLM 抽取   │状态视图/ │ 处置/忽略   │ Report DTO │
│ 版本链    │  证据/快照  │检测器/指纹│ 原因码/权威 │ JSON/HTML  │
├──────────┴────────────┴──────────┴────────────┴────────────┤
│  providers：OpenAI 兼容 LLM / Embedder（云端 / 网关 / Ollama） │
├─────────────────────────────────────────────────────────────┤
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
| `truthlayer.resolution` | 漂移查询、人工处置（四决策/原因码/权威事实）、忽略 | service 只 flush，决策不可变更（one-shot） |
| `truthlayer.reporting` | Report DTO、报告组装、CI 阈值策略、中文叙述、确定性 JSON、Jinja2 HTML | DTO 是 CLI/JSON/HTML 的唯一事实来源 |
| `truthlayer.evaluation` | Golden QA schema、内存态构造、期望匹配、P/R/F1/FPR 指标与 Phase 0 Gate | 旁路质量层：只依赖检测器与纯 dataclass，不碰数据库/LLM/ORM |
| `truthlayer.providers` | OpenAI 兼容的 LLM/Embedder 实现（云端、网关、Ollama 通用） | 协议化，可替换 |
| `truthlayer.cli` | Typer 命令、事务提交、终端输出 | 薄壳，禁止写业务规则 |

### 两条事务纪律

1. **service 层只 `flush` 不 `commit`**：一次 scan 是 ingestion → extraction →
   detection（报告 DTO 在同一事务内组装）串起来的单一事务，由 CLI 在全部成功后
   统一提交并在提交后写报告文件，失败整体回滚。resolution 命令则是独立短事务。
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
├── resolutions         （drift_id UNIQUE：一条漂移至多一次处置，one-shot）
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

- LLM 输出受版本化 schema（`fact-extract-v3`）约束，支持 strict json_schema →
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

### 4.4 处置（resolution）

漂移有三种状态：`open → ignored / resolved`，由 `ResolutionService` 管理：

- **四种决策**：`accept_newer`（采纳新事实）、`keep_old`（保留旧事实）、
  `manual_override`（人工裁定）、`false_positive`（误报）；
- **受控原因码**（`ReasonCode` 枚举，10 个）：自由文本原因与结构化原因码并存，
  原因码用于后续统计，文本用于人读；
- **权威事实规则**：accept_newer/keep_old 默认指向新/旧事实，可显式覆盖，
  但覆盖值必须是该漂移自身的 old/new fact；文档级漂移（superseded）无 fact，
  禁止指定；manual/false_positive 不产生权威事实；
- **one-shot 不可变更**：`resolutions.drift_id` 为 UNIQUE，已 resolved 的漂移
  不能再 resolve 或 ignore（误处置需数据层修正，审计留痕不被静默覆盖）；
  ignore 幂等（重复 ignore 不报错），但 resolved 永远不能转 ignored；
- Phase 0 仅支持 `scope=single`、`pattern_jsonb=NULL`（无批量模式处置）；
- 忽略原因存入 `drifts.detail_jsonb["ignore_reason"]`，状态留在 drift 表。

处置的核心意义在 **Remember 闭环**：指纹去重比对的是全量历史（含已处置），
被忽略/处置过的问题在后续扫描中永远显示 already known，不再打扰；CI 门禁
也只统计 open 漂移。

### 4.5 报告（reporting）

终端输出、JSON 文件、HTML 文件共享同一个 Pydantic DTO（`ReportDTO`），
不允许任何渲染层自行查库或拼装规则：

```text
ReportBuilder（查库组装 DTO，唯一接触 ORM 的报告组件）
   └─▶ ReportDTO(summary, issues)
          ├─▶ render_json   确定性序列化（sort_keys、ensure_ascii=False）
          └─▶ render_html   Jinja2 模板（autoescape 显式包含 .j2）
```

- **每个 issue**：五要素（标题 / 为什么是问题 / 建议操作 / 建议决策、
  五类型各自的中文叙述）+ 新旧事实片段 + 新旧来源 + 去重后的逐字证据 +
  处置记录；证据悬空（文档/事实后来被删除，FK SET NULL）不导致报告崩溃；
- **summary**：文档/实体/事实计数、本次新增与抑制数、三状态计数、
  按类型与严重级分布、本次扫描使用的知识哈希；
- **CI badge**：纯函数策略 `severity ≥ fail_on` 即阻断（阈值含等号，`none`
  永远放行），结果写入 DTO 并由 CLI 映射为退出码 `1`；
- HTML 是单文件内联样式、零外部依赖，可直接作为 CI artifact 归档；
- `check` 只渲染最近一次 scan 持久化的状态（不调用 LLM，不花 token），
  scan 则在事务提交后写文件（保证报告反映的是已落库状态）。

### 4.6 评测（evaluation）

评测是**旁路质量层**，回答"检测器裁决得准不准"，而不是再跑一遍扫描：

```text
examples/qa_cases/*.yaml
   └─▶ QACase（文档/实体/事实 + 规则阈值 + 期望漂移，三类标签）
          └─▶ build_world   直接构造 KnowledgeState / DetectionContext
                 │            （id 由 case id + 局部 id 经 uuid5 派生，确定可重放）
                 └─▶ default_detectors().detect(...)   ← 复用真实检测器
                        └─▶ match_findings   期望 ↔ 候选（类型必需，其余定位器收窄）
                               └─▶ compute_metrics  P / R / F1 / FPR + Gate
```

- **零外部依赖**：检测器入口本就是 frozen dataclass `KnowledgeState`，因此用例
  无需数据库、无需 LLM，毫秒级完成；这恰好评测的是建议书 #22/#23 划定的
  "确定性裁决核心"（LLM 只提候选、向量只召回），任何人 clone 即可复跑；
- **三类用例**：positive 必须声明 ≥1 条期望漂移，negative / ambiguous_negative
  必须零期望——该不变量在 schema 层强制，防止写错用例"刷指标"；
- **指标口径**：finding 级 Precision / Recall / F1 以每条期望漂移为标注正例
  （TP/FP/FN）；FPR 在**用例级**良性场景（正常 + 疑似冲突）上统计
  "被误报的良性用例 / 良性用例总数"，两种口径分别透明报告，不混用；
- **Gate 严格不等号**：P>80% / R>70% / F1>75% / FPR<30%，另要求正例与良性用例
  均非空，防止空集作弊；同时输出每个检测器的独立 P/R 与误报分类；
- **真实世界 HCR**（人工确认率）属线上持续度量，不纳入离线 Gate，二者边界在
  README 中明确，不用离线高分暗示线上表现。

`truthlayer eval` 是薄壳：加载用例 → 跑 harness → 算指标 → 终端/JSON/Markdown，
退出码 `0` Gate 通过 / `1` 未达标 / `2` 用例加载或产物写入失败。

## 5. 配置与凭据

- `.truthlayer.yaml` 全部字段由 Pydantic 强校验（`extra="forbid"`，未知键即报错），
  包括 sources 权威度、过期阈值、severity 覆盖、CI fail_on、显式版本链、
  extraction/embedding 双端点槽位；
- **凭据只走环境变量**：配置里只允许出现 `api_key_env` 的变量名；
- LLM 与 embedding 可以是同一供应商（一个 Key）、两家供应商（两个 Key）
  或本机 Ollama（零 Key）；embedding 槽位整体可选，缺向量不阻塞知识落库；
  向量列不锁定维度，实际维度记入 scan_run。

## 6. 错误处理

领域层定义带语义的错误类型（配置/解析/Provider/数据库/领域校验/用户输入等）。
CLI 退出码（稳定契约）：`0` 成功；`1` fail_on 门禁失败（存在达到阈值的 open
漂移，已忽略/已处置不阻断）；`2` 系统错误（配置非法、数据库不可用、存在解析
失败文件、报告写盘失败）。系统错误的优先级高于门禁：`2 > 1 > 0`。失败的 scan
也会在 `scan_runs` 留痕（status=failed、error_message）。

## 7. 测试架构

- **单元测试**（`tests/unit/`，无需数据库）：纯函数与检测器为主。检测器测试使用
  内存中的 frozen 状态工厂，刻意覆盖负例（同文档 tier、149==149.0、时间窗不重叠、
  通用属性误归并等）；
- **集成测试**（`tests/integration/`）：每个会话使用一次性隔离库 `<dbname>_it`，
  自动 `alembic upgrade head` / 结束 `downgrade base`，直接构造 ORM 行而不调用 LLM，
  覆盖五类型漂移的检出、字段与跨扫描去重、Resolution 全生命周期（含重复处置防护、
  权威事实规则）、报告组装以及 drift/resolve CLI 命令；
- **Ollama 冒烟测试**：默认 skip，`TRUTHLAYER_RUN_OLLAMA=1` 时对真实模型端到端验证，
  CI 无 Ollama 不受影响；
- **Golden QA 回归**（`tests/unit/evaluation/`）：加载 `examples/qa_cases` 全量用例，
  在纯内存态跑真实检测器，断言用例数量区间（50–100）、三类齐全、五类型均有正例，
  且整体持续满足 Phase 0 Gate。无数据库/LLM，随普通单元测试一起强制执行。

## 8. 关键设计决策（贡献者必读）

1. **Evidence First**：没有逐字可溯源证据的事实不允许存在；
2. **LLM 只做候选**：模型输出永远是"候选"，准入由确定性规则裁决；
3. **Embedding 只召回不裁决**：任何漂移结论不得仅由向量相似度得出；
4. **确定性优先**：知识哈希、检测指纹、切块编号、文件排序全部可复现；
5. **显式优于猜测**：版本链只认配置声明，歧义默认拒绝并告警；
6. **CLI 是薄壳**：业务逻辑只允许出现在 domain/service 层；
7. **处置即审计**：决策 one-shot 不可改，原因码受控、自由说明留痕，处置不删除证据；
8. **单一报告模型**：终端/JSON/HTML 必须渲染同一个 DTO，渲染层不做判定、不查库；
9. **先写用例再改规则**：调整检测器前先补业务语义的正/负/模糊 QA 用例；离线 Gate
   只度量确定性裁决层，线上 HCR 单独采集，两者不互相替代或美化。
