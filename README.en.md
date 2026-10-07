# TruthLayer

> 🌐 Language: [简体中文](./README.md) · **English**

**Continuously verify the enterprise knowledge your AI relies on — a CLI-first Knowledge Drift Detector.**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-green)](./LICENSE)
[![Tests](https://img.shields.io/badge/tests-214%20passed-success)](#testing)

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
| `truthlayer eval [--cases …]` | Run the golden QA set and compute the Phase 0 Gate metrics (no DB, no LLM) |
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

## Evaluation & the Phase 0 Gate

TruthLayer measures the **adjudication quality of its deterministic core** with a
golden QA set. Each case is a self-contained scenario (documents / entities / facts
+ rule thresholds + expected drifts); at evaluation time it is mapped straight into
an in-memory knowledge state and fed to the **real detectors** — no database, no LLM,
millisecond-fast and fully deterministic, so anyone can re-run it after cloning and it
is enforced as a regression test on every run:

```powershell
# Print metrics to the terminal; optionally emit deterministic JSON and Markdown
.\.venv\Scripts\truthlayer eval --cases .\examples\qa_cases `
  --output eval.json --markdown eval.md
```

Cases fall into three categories: **Positive** (real drift, must be flagged),
**Negative** (healthy knowledge, must never be flagged) and **Ambiguous Negative**
(looks like a conflict but differs in time / scope / applicable subject).

Phase 0 Gate thresholds (strict inequalities):

| Metric | Meaning | Threshold |
|---|---|---|
| Precision | Share of reported findings that are real drift | > 80% |
| Recall | Share of real drift that is detected | > 70% |
| F1 | Harmonic mean of Precision and Recall | > 75% |
| FPR | Share of benign (normal / ambiguous) scenarios falsely alarmed | < 30% |

The bundled set lives in [examples/qa_cases](./examples/qa_cases) (59 cases:
28 positive / 17 negative / 14 ambiguous-negative, covering all five drift types).
On the deterministic detection core it achieves **Precision / Recall / F1 = 100% and
FPR = 0% — Gate passed**, with per-detector P/R and a false-positive breakdown.

Note the boundary: this Gate covers the **deterministic adjudication layer** (the LLM
only proposes candidates and embeddings only recall; every verdict is traceable).
End-to-end extraction quality is covered by the Ollama smoke test; the real-world
**HCR (Human Confirmation Rate)** is collected continuously in production and is not
mixed with these offline metrics. When changing detector rules, add business-semantics
positive / negative / ambiguous cases first, then adjust the rules.

### End-to-end Human Confirmation Rate (HCR) baseline

Beyond the offline Gate, we also run the **full real-LLM extraction + detection**
pipeline over **24 synthetic semi-realistic documents** (with 8 deliberately planted
problem scenarios G1–G8), then manually judge each reported drift to quantify the
end-to-end cost of LLM extraction. The corpus, the planted scenarios and the auditable
labels/scoring artifacts live in [examples/eval_corpus](./examples/eval_corpus)
(**non-deterministic, not a CI gate**; run it keyless against a local Ollama, or point
at any OpenAI-compatible online endpoint via `TRUTHLAYER_E2E_LLM_BASE_URL` /
`TRUTHLAYER_E2E_LLM_MODEL` — the key is read only from `TRUTHLAYER_LLM_API_KEY`):

| Run (2026-10-06) | Model (prompt) | Reported | HCR | actionable | Planted recall | Chunks failed (entities/facts) |
|---|---|---|---|---|---|---|
| run1 | qwen2.5:7b (v1) | 32 | **62.5% (20/32)** | 56.2% | **8/8** | 2 (59/90) |
| run3 | qwen2.5:7b (v3) | 17 | **100% (17/17)** | 94.1% | 6/8 | 1 (70/99) |
| run5 | deepseek-flash online (v3) | 39 | **89.7% (35/39)** | 84.6% | 7/8 | **0 (84/171)** |
| run6 | deepseek-flash online (v4) | 43 | **90.7% (39/43)** | 88.4% | **8/8** | **0 (65/165)** |

Across all four runs the **deterministic Golden Gate stayed at P/R/F1 = 100%, FPR = 0%**
— whenever extraction supplies the correct entities, the detectors neither false-alarm nor
miss. The end-to-end differences are entirely LLM-extraction non-determinism (the run-to-run
swing in entities 59→84→65 and facts 90→171→165 is direct evidence).

Tuning wins and remaining gaps:

- **Age-based false alarms addressed**: run1's possibly_stale HCR was only 15.4% (age-only
  heuristics hit current price lists / evergreen clauses, and a recurring "every June–August"
  window was mistaken for an expiry). After two deterministic suppressions (latest version in
  a chain R1; open-ended annual rates within their edition year R2) plus the prompt-v3 date
  rules, possibly_stale HCR is **100%** in run3, run5 and run6.
- **The stronger online model recovered two extraction losses**: deepseek-flash had 0 failed
  chunks, correctly wrote the heat-allowance "last reviewed 2025-07-15" into `observed_at`
  (G6 surfaces as possibly_stale) and kept "Xingyun Support Center / (Hi-Tech Branch)" as two
  entities (G8 surfaces as duplicate).
- **R8 shipped and verified in v0.1.1**: run5's single miss was **G4** (direct-sales 800 vs
  reseller 950 cross-source conflict). The extractor labelled the same-name "data migration
  service" with **different entity types** (`service` vs `product`) in the two channel sheets;
  entity resolution keys on (workspace + canonical name + type), so it fragmented into two
  entities and the conflict detector — grouping by (entity, predicate) — never saw the
  contradiction. The same fragmentation also fired 2 duplicate false alarms on deliberately
  identical cross-channel prices. v0.1.1 fixes this with a **controlled type-equivalence class**
  (merge same-name entities only when the declared types drift within one sellable-offering
  class, and only when the name is unique; never merge across semantic categories): in run6 the
  service merges into one entity, 800 vs 950 correctly fires as a conflict, the false alarms
  vanish, and **planted recall reaches 8/8**. A side effect of merging was 3 "channel =
  direct/reseller" conflicts — channel is a legitimately **multi-valued** attribute a single
  offering may hold at once, so declaring it via the existing `multi_valued_predicates: [渠道]`
  config removes them (**config-corrected run6: 40 reports, HCR 97.5%, actionable 95%**).
- **Remaining limits (R6/R7/R9)**: run6 keeps just 1 sporadic extractor false alarm — a Q2 promo
  window `valid_to` attached to a regular price "list 199" (also seen in run2/run5, logged as an
  extractor backlog item). **Over-merging** of differently-named entities (R7) and cross-source
  strong-identifier normalization remain long-term work, currently backstopped by the duplicate
  detector's "recall candidates → deterministic confirmation".

Reproduce with `scripts/e2e_eval.py`, then score against human labels with
`scripts/e2e_score.py --run <dir> --labels <dir>/labels.json`. The curated run1/run5/run6 labels
and scores live in [examples/eval_corpus/results](./examples/eval_corpus/results).

`eval` exit codes: `0` Gate passed, `1` Gate failed, `2` cases could not be loaded or
an artifact could not be written.

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

The suite currently contains **214 passing tests**: unit tests cover
normalization/hashing/entity resolution/evidence validation, every decision
branch and negative case of the four detectors, the CI threshold matrix and
deterministic serialization / HTML escaping of the report DTO, evaluation
matching / metrics / Gate threshold boundaries, and the **dependency-free golden
QA regression** (59 cases across all three categories that must keep passing the
Gate); integration tests verify the detection, persisted fields and cross-scan
fingerprint dedup of all five drift types, the full Resolution lifecycle
(resolve/ignore/double-resolve guards), report assembly and the drift/resolve CLI
commands on real PostgreSQL. One additional Ollama end-to-end smoke test is
skipped by default (set `TRUTHLAYER_RUN_OLLAMA=1` to run it).

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
- [x] Sprint 6: golden QA set (59 cases / 3 categories), P/R/F1/FPR metrics with dependency-free regression, `truthlayer eval`

**The Phase 0 Gate for the deterministic core is passed** (golden set:
Precision / Recall / F1 = 100%, FPR = 0%, and this 100/0 held across every real-LLM
end-to-end run). Real-LLM end-to-end HCR evolved over four runs (qwen2.5:7b v1 62.5% /
8-of-8 recall → v3 100% / 6-of-8 → online deepseek-flash v3 89.7% / 7-of-8 → v4 90.7% / 8-of-8),
confirming the end-to-end weak spot is non-determinism in LLM entity-identity and date
extraction, whose cross-source type-fragmentation (R8) is now fixed in v0.1.1. **The first
release, v0.1.0 (a CLI-first experimental edition), is published, with the quality-polish
release v0.1.1 following, with the attribute semantic resolution and detector-hardening
release v0.2.0 now published.** The current version is `0.2.0`.

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
