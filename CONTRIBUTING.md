# 贡献指南

> 🌐 语言：**简体中文** · [English](./CONTRIBUTING.en.md)

感谢你对 TruthLayer 的兴趣！本文说明如何搭建开发环境、项目的编码纪律，
以及提交 Issue / PR 的流程。

## 行为准则

参与本项目即表示你同意保持友善与专业：尊重不同背景的贡献者，就事论事，
不接受任何形式的骚扰或人身攻击。

## 1. 开发环境

需要：Python 3.11+、PostgreSQL 15+（含 [pgvector](https://github.com/pgvector/pgvector)）。

```powershell
git clone git@atomgit.com:XingTuLink/TruthLayer.git
cd TruthLayer

python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\python -m pip install -e ".[dev,parsers,openai]"
# macOS / Linux
# .venv/bin/pip install -e ".[dev,parsers,openai]"
```

初始化本地开发库：

```powershell
$env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://用户:密码@localhost:5432/truthlayer"
.\.venv\Scripts\python scripts/dev_init_db.py
.\.venv\Scripts\alembic upgrade head
```

> pgvector 安装提示：Linux 用发行版包（如 `postgresql-16-pgvector`）；
> macOS 可用 Homebrew；Windows 需按 pgvector 官方说明在 VS 构建工具下编译。

想零 Key 体验完整扫描，可安装 [Ollama](https://ollama.com/) 并
`ollama pull qwen2.5:7b bge-m3`，然后运行
`.\.venv\Scripts\truthlayer scan .\examples\demo_kb`。

## 2. 跑测试

```powershell
# 单元测试：无需数据库，PR 必须保持全绿
.\.venv\Scripts\python -m pytest tests/unit -q

# 全量：集成测试会自动创建/销毁一次性隔离库 <dbname>_it，不碰开发库
$env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://..."
.\.venv\Scripts\python -m pytest -q
```

## 3. 代码组织

- `src/truthlayer/domain/`：纯领域规则，禁止导入 ORM、CLI、第三方厂商 SDK；
- `src/truthlayer/ingestion|extraction|detection/`：业务能力包，service 只
  `flush` 不 `commit`；
- `src/truthlayer/cli/`：Typer 薄壳，只做参数解析、事务控制与输出；
- `tests/unit/` 与 `tests/integration/` 分离：不依赖数据库的测试一律放 unit。

新增/变更表结构时，必须同时提供 Alembic 迁移（可 `alembic upgrade head` 与
`downgrade base` 双向干净运行）。

## 4. 设计红线（不可违反）

以下原则是项目的立身之本，违反它们的 PR 不会被合并：

1. **Evidence First**：每条事实必须锚定可定位的原文证据；
2. **LLM 只做候选**：模型输出必须经过确定性校验才能落库，模型不做任何最终裁决；
3. **Embedding 只召回不裁决**：漂移结论不得仅依据向量相似度；
4. **确定性可复现**：哈希、指纹、排序、切块编号不得依赖随机数或集合迭代顺序；
   本地时区也不能影响结果（统一 UTC 处理）；
5. **显式优于猜测**：版本链只认配置声明；解析/实体歧义默认拒绝或告警；
6. **CLI 零业务逻辑**：规则只写在 domain/service 层，方便未来 API 直接复用；
7. **凭据只走环境变量**：`.truthlayer.yaml` 中只能出现 `api_key_env` 变量名，
   永远不提交真实 Key、`.env` 或真实企业数据。

## 5. 编码风格

- 面向 Python 3.11+，全面使用类型标注，公共函数写清楚 docstring；
- 模块/类/函数命名直白优先；错误必须使用 `truthlayer.domain.errors` 中
  带语义的错误类型；
- 新行为先写/同步测试：bug 修复请附上能复现问题的回归测试；
- 检测器新增判定分支时，同时补**正例与负例**（尤其是"不该报"的场景）；
- 不引入不必要的依赖；能用标准库解决的不引第三方包。

## 6. 提交信息

使用简洁的祈使句，建议带模块前缀：

```text
detection: 收紧 duplicate 的单事实鉴别力条件
ingestion: 修复 XLSX 整数被渲染为浮点的问题
docs(readme): 补充 Ollama 配置说明
test(stale): 增加无年龄信号不判过期的负例
```

一个 PR 保持主题聚焦，避免混入无关重构。

## 7. Issue 与 PR 流程

**Issue**：

- Bug：请给出复现命令、期望/实际结果、Python 与 PostgreSQL 版本、完整报错；
- 功能建议：说明解决的问题与典型使用场景，不必预先写实现方案。

**Pull Request**：

1. 从最新主干切分支，标题概括变更；
2. 正文说明"做了什么、为什么、如何验证"，关联相关 Issue；
3. 确保 `pytest -q` 全绿；涉及数据库的变更在本地验证迁移可上可下；
4. 用户可见行为变化请同步更新 README 或 [docs/architecture.md](./docs/architecture.md)；
5. 收到 review 意见后推送到同一分支即可，无需 force-push 重写历史。

## 8. 许可证

提交 PR 即表示你同意：你的贡献以 [AGPL-3.0](./LICENSE) 授权给本项目，
且你有权做出上述授权。

## 9. 安全问题

请勿在公开 Issue 中披露安全漏洞或任何凭据。安全问题请通过仓库维护者联系方式
私下报告，我们会尽快响应。
