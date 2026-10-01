# TruthLayer

> 🌐 Language: [简体中文](./README.md) · **English**

**Continuously verify the enterprise knowledge your AI relies on — a CLI-first Knowledge Drift Detector.**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-green)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-193%20passed-success)](#testing)

Policies, price lists and product manuals keep being revised, but AI assistants
often quote outdated versions: conflicting figures across documents, policies
superseded by newer editions, numbers nobody has re-checked for ages, and the
same entity written in several different ways. TruthLayer extracts the knowledge
scattered across documents into structured facts anchored to **verbatim source
evidence**, and deterministically detects such "knowledge drift" on every scan —
raising traceable warnings before wrong answers reach users.

- **CLI-first**: one `truthlayer scan` runs the entire pipeline; plug it straight into CI (no daemon required)
- **Evidence First**: every fact must be anchored to a verbatim quote; nothing enters the knowledge base without evidence
- **LLMs only propose, rules decide**: models extract candidates; all conflict/staleness verdicts come from deterministic rules
- **Embeddings only recall, never adjudicate**: vectors surface candidates; conclusions never rest on similarity scores
- **Runs fully local**: one OpenAI-compatible protocol covers cloud gateways and local [Ollama](https://ollama.com/), including zero-API-key setups
- **Closed-loop resolution**: human decisions (accept/keep/false-positive) and ignores are remembered by fingerprint — resolved issues never nag again
- **Readable reports + CI gate**: self-contained HTML and deterministic JSON share one report model; the `fail_on` threshold maps directly to an exit code

---

## Contents

- [What it detects](#what-it-detects)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [CLI commands](#cli-commands)
- [Testing](#testing)
- [Documentation](#documentation)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## What it detects

| Drift type | Meaning | Typical case |
|---|---|---|
| `conflict` | The same subject and predicate have contradictory values that are both currently valid | Seat-zone policy differs between two policy documents |
| `confirmed_stale` | The fact has expired, or its source document was explicitly superseded | The 2026 price list explicitly replaces the 2025 one |
| `possibly_stale` | A fact has not been re-checked for too long (age signal only, warning level) | A pricing fact with no update for over 90 days |
| `superseded` | A document has an explicitly declared newer version | `price_list_2025.csv → price_list_2026.csv` |
| `duplicate` | Two entities with name variants share identifying facts and may be unmerged aliases | "ACME CRM" vs. "ACME CRM Pro" |

Every finding carries a severity (warning / medium / high / critical), a
confidence score, an AI-impact level, and full pointers to the **old/new facts,
source documents and verbatim evidence**.

Already-reported findings are deduplicated across scans via deterministic
fingerprints — you are never notified about the same issue twice.

## How it works

```text
 Enterprise documents (TXT/MD/CSV/PDF/DOCX/XLSX)
        │
        ▼
 discover → parse → normalize → chunk
        │
        ▼
 LLM candidate extraction (entities / facts / relations)
        │  structured schema + deterministic validation
        ▼
 Entity / Fact / Evidence persisted (every fact needs source evidence)
        │
        ├──▶ chunk / entity embeddings (pgvector, any dimension)
        ├──▶ immutable snapshots + deterministic Knowledge Hash
        │
        ▼
 four deterministic detectors → five drift types → persistence / dedup / summary
```

## Quick start

### Requirements

- Python 3.11+
- PostgreSQL 15+ with the [pgvector](https://github.com/pgvector/pgvector) extension installed
- (Optional) Local [Ollama](https://ollama.com/) with `qwen2.5` and `bge-m3` pulled for a fully keyless run; any OpenAI-compatible cloud endpoint works too

### 1. Install

```powershell
git clone git@github.com:XingTuLink/TruthLayer.git
cd TruthLayer

python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,parsers,openai]"
```

### 2. Initialize the database

```powershell
$env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://user:password@localhost:5432/truthlayer"

.\.venv\Scripts\python scripts/dev_init_db.py   # idempotent database creation
.\.venv\Scripts\alembic upgrade head            # schema + pgvector extension
```

### 3. Run the bundled demo knowledge base

The repository ships with a fictional company "ACME": two policies (leave and
travel-expense), two cross-year price lists, and one internal draft that must be
ignored. Its config points at local Ollama, so no API key is needed:

```powershell
# Ingestion + config validation only
.\.venv\Scripts\truthlayer check .\examples\demo_kb

# Full scan: extract facts → embeddings → snapshot → drift detection
.\.venv\Scripts\truthlayer scan .\examples\demo_kb
```

The output shows extraction statistics, the deterministic knowledge hash and the
drift-detection summary. Re-scanning unchanged content keeps the hash identical
and reports all drift as already known (idempotent).

## Configuration

Place `.truthlayer.yaml` at the root of your knowledge base (full example:
[examples/demo_kb/.truthlayer.yaml](./examples/demo_kb/.truthlayer.yaml)):

```yaml
workspace:
  name: acme-demo

sources:                        # source dirs + authority score + source type
  - path: ./docs/policies
    authority: 0.95
    type: policy
  - path: ./docs/pricing
    authority: 0.9
    type: pricing

rules:
  stale_after_days: 365         # re-check threshold for ordinary facts
  pricing_stale_days: 90        # tighter threshold for pricing facts

severity:                       # override conflict severity per source type
  pricing_change: high
  policy_change: critical

ci:
  fail_on: critical             # CI failure threshold: none|critical|high|medium|warning

version_mapping:                # explicit chains: declared only, never guessed from filenames
  - group: product_pricing
    files:
      - path: 产品价格表_2025.csv
        label: "2025"
      - path: 产品价格表_2026.csv
        label: "2026"
        supersedes: 产品价格表_2025.csv

ignore:
  - "**/internal-test/**"

extraction:                     # any OpenAI-compatible endpoint (cloud / Ollama)
  provider: openai_compatible
  model: qwen2.5:7b
  base_url: http://localhost:11434/v1

embedding:
  provider: openai_compatible
  model: bge-m3
  base_url: http://localhost:11434/v1
  dimensions: 1024
```

**Credential rule**: API keys are read only from environment variables
(`TRUTHLAYER_LLM_API_KEY` / `TRUTHLAYER_EMBEDDING_API_KEY`, rename via
`api_key_env`) and never written into config files; localhost endpoints need no key.

## CLI commands

| Command | Description |
|---|---|
| `truthlayer check <path>` | Validate config and run document ingestion (parse/chunk/version chains) |
| `truthlayer scan <path>` | Full scan: ingestion + extraction + embeddings + snapshot + drift detection |
| `truthlayer drift list [--status …] [--type …] [--workspace …]` | List drifts (open by default) |
| `truthlayer drift show <id>` | Full detail: old/new facts, verbatim evidence, AI impact, recommended action |
| `truthlayer drift ignore <id> [--reason …]` | Ignore (remembered by fingerprint; never re-reported) |
| `truthlayer resolve <id> --decision …` | Record a human decision — see below |
| `truthlayer --version` | Version info |

Reports and the CI gate:

```powershell
# Both scan and check emit reports; check spends no LLM tokens — it renders
# the state persisted by the latest scan.
truthlayer scan  .\examples\demo_kb --html report.html --output report.json
truthlayer check .\examples\demo_kb --html report.html

# Resolve a conflict: accept the newer fact with a controlled reason code
truthlayer resolve <drift-id> --decision accept_newer `
  --reason-code source_updated --reason "2026 policy is now in force"
```

Four decisions (Phase 0 supports `scope=single` only):

| decision | meaning | default authority_fact |
|---|---|---|
| `accept_newer` | The newer knowledge wins | new fact (override with `--authority-fact-id`) |
| `keep_old` | The old knowledge still holds | old fact |
| `manual_override` | Human adjudication (neither side is final) | none |
| `false_positive` | False alarm (e.g. co-existing tiers, extraction error) | none |

`--reason-code` uses a controlled vocabulary (`newer_version`, `source_updated`,
`still_valid`, `lower_authority`, `multi_valued`, `extraction_error`,
`detector_noise`, `duplicate_confirmed`, `manual`, `other`); free text goes into
`--reason`; the operator defaults to the OS user and can be set with `--by`.

Stable exit-code semantics:

- `0` pass;
- `1` **open** drifts meet or exceed the `ci.fail_on` threshold (ignored/resolved never block);
- `2` system errors (invalid config, database unavailable, files failed to parse).

## Testing

```powershell
# Unit tests (no database needed; deterministic and fast)
.\.venv\Scripts\python -m pytest tests/unit

# Full suite (PostgreSQL integration tests automatically use a throwaway
# database named <dbname>_it, created and dropped per session — your dev
# database is never touched)
$env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://..."
.\.venv\Scripts\python -m pytest
```

The suite currently contains **193 passing tests**: unit tests cover
normalization/hashing/entity resolution/evidence validation, every decision
branch and negative case of the four detectors, the CI threshold matrix and
deterministic serialization / HTML escaping of the report DTO; integration tests
verify the detection, persisted fields and cross-scan fingerprint dedup of all
five drift types, the full Resolution lifecycle (resolve/ignore/double-resolve
guards), report assembly and the drift/resolve CLI commands on real PostgreSQL.
One additional Ollama end-to-end smoke test is skipped by default
(set `TRUTHLAYER_RUN_OLLAMA=1` to run it).

## Documentation

- Architecture: [English](./docs/architecture.en.md) · [简体中文](./docs/architecture.md)
  — layering, data model, detection principles and key design decisions
- Contributing: [English](./CONTRIBUTING.en.md) · [简体中文](./CONTRIBUTING.md)
  — development setup, coding principles, testing requirements and PR workflow

## Roadmap

TruthLayer is currently in **Phase 0 (CLI edition)**, iterating in six sprints:

- [x] Sprints 1–2: scaffold, domain model, six document formats, version chains
- [x] Sprint 3: LLM knowledge extraction, Evidence First, embeddings, immutable snapshots
- [x] Sprint 4: drift engine (four detectors / five types, fingerprint dedup)
- [x] Sprint 5: resolution workflow, `drift`/`resolve` CLI, CI fail_on, HTML/JSON reports
- [ ] Sprint 6: evaluation corpus and metric acceptance (Precision / Recall / FPR)

**v0.1 will be formally released after the Phase 0 Gate is passed.** The current
version is `0.0.1` (under active development).

## Contributing

Issues and PRs are welcome! Please read the [contributing guide](./CONTRIBUTING.en.md)
first: development setup, the non-negotiable design red lines (Evidence First,
LLMs only propose, embeddings never adjudicate, etc.), testing expectations and
commit conventions.

## License

This project is licensed under the GNU Affero General Public License v3.0
(AGPL-3.0), © 西安栈上月明软件科技有限公司 (Xi'an Zhanshang Yueming Software
Technology Co., Ltd.).

Please note the key AGPL obligation: if you modify this project and offer it to
users over a network (including SaaS / hosted forms), you must make the complete
modified source code available to those network users under the same license.

Official repositories:

- AtomGit: `git@atomgit.com:XingTuLink/TruthLayer.git`
- GitHub: `git@github.com:XingTuLink/TruthLayer.git`
- Gitee: `https://gitee.com/XingTuLink/truth-layer`
