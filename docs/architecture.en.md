# Architecture

> 🌐 Language: [简体中文](./architecture.md) · **English**

This document is for contributors who want to understand the design or work on
the codebase. It covers layering, the data model, the processing pipeline and
the key design decisions.

## 1. Overview

TruthLayer is a CLI-first local/CI tool: one scan turns enterprise documents
into evidence-backed structured knowledge and runs deterministic drift
detectors over the current knowledge state. No daemon is required, and the
transaction boundary is controlled by the CLI. The same service layer can later
be reused directly by an API.

```text
┌─────────────────────────────────────────────────────────────┐
│  CLI (thin Typer shell: args / transactions / output only)   │
├─────────────────────────────────────────────────────────────┤
│ ingestion → extraction → detection → resolution → reporting │
├──────────┬────────────┬──────────┬────────────┬────────────┤
│ ingestion│ extraction │detection │ resolution │ reporting  │
│ parse/   │ LLM        │ state /  │ resolve /  │ Report DTO │
│ chunk    │ extraction │detectors │ ignore     │ JSON/HTML  │
│ versions │ snapshots  │fingerpr. │ reason/    │ CI badge   │
│          │            │          │ authority  │ narratives │
├──────────┴────────────┴──────────┴────────────┴────────────┤
│  providers: OpenAI-compatible LLM / Embedder (cloud/Ollama)  │
├─────────────────────────────────────────────────────────────┤
│  domain: enums / FactClaim / Evidence (pure, zero-infra)     │
├─────────────────────────────────────────────────────────────┤
│  SQLAlchemy 2 ORM + Alembic  ·  PostgreSQL 15 + pgvector     │
└─────────────────────────────────────────────────────────────┘
```

## 2. Layers

| Package | Responsibility | Dependency rules |
|---|---|---|
| `truthlayer.domain` | Enums, error types, `FactClaim`/`Evidence` value objects and all invariant checks | No ORM, CLI, or vendor SDK imports |
| `truthlayer.db` | SQLAlchemy ORM (13 tables), session, Alembic migrations | Called only by the service layer |
| `truthlayer.ingestion` | Discovery, six parsers, encoding fallback, normalization, hashing, chunking, explicit version chains | Knows nothing about LLMs |
| `truthlayer.extraction` | Extraction schema/prompts, entity resolution, fact/evidence persistence, knowledge hash, immutable snapshots | Calls models through the providers abstraction |
| `truthlayer.detection` | Knowledge-state loading, candidates/fingerprints, four detectors, orchestration service | Detectors never touch a session or the ORM |
| `truthlayer.resolution` | Drift queries, human resolution (four decisions / reason codes / authority fact), ignore | Services only flush; decisions are immutable (one-shot) |
| `truthlayer.reporting` | Report DTO, assembly, CI threshold policy, Chinese narratives, deterministic JSON, Jinja2 HTML | The DTO is the single source of truth for CLI/JSON/HTML |
| `truthlayer.evaluation` | Golden QA schema, in-memory world builder, expectation matching, P/R/F1/FPR metrics and the Phase 0 Gate | Side-car quality layer: depends only on detectors and plain dataclasses — no DB, LLM or ORM |
| `truthlayer.providers` | OpenAI-compatible LLM/Embedder implementations (cloud, gateways, Ollama) | Protocol-based and replaceable |
| `truthlayer.cli` | Typer commands, transaction commit, terminal output | Thin shell; no business rules allowed |

### Two transaction disciplines

1. **Services only `flush`, never `commit`**: a scan is one transaction spanning
   ingestion → extraction → detection (the report DTO is assembled inside the
   same transaction). The CLI commits once after everything succeeds and writes
   report files post-commit; any failure rolls the whole run back. The
   resolution command is a separate short transaction.
2. **Detectors read only immutable views**: `KnowledgeState` (frozen dataclasses)
   is loaded once from the ORM before detection starts. Detectors cannot write
   to the database or depend on query side effects, which makes results
   replayable.

## 3. Data model

13 core tables (see migration `alembic/versions/0001_initial.py`):

```text
workspaces
├── document_groups ── documents ── chunks
│     (version groups)     │  ↑ previous_version_id (explicit supersession chain)
│                          ├── facts ── (evidence inlined via JSONB fields)
│                          └── entities ── entity_aliases
├── scan_runs           （full audit trail of every scan, incl. detector_version)
├── drifts              （polymorphic target: fact or document, deliberately no FK)
├── resolutions         （drift_id UNIQUE: at most one decision per drift — one-shot)
└── snapshots + snapshot_facts / snapshot_entities (immutable freeze)
```

Key constraints enforced in the database:

- **Fact object XOR**: exactly one of `object_entity_id` / `object_value` must
  exist (CHECK);
- **Time windows**: `valid_from <= valid_to` (CHECK);
- confidence / authority_score bounded to 0–1;
- file-level idempotency: `UNIQUE(workspace_id, file_hash)`;
- Drift's `target_id` deliberately has no foreign key (polymorphic reference to
  fact/document; the service layer validates it).

## 4. Scan pipeline

### 4.1 Ingestion

Discovery (deterministic ordering, ignore globs, overlapping-source dedup) →
raw-byte SHA-256 (`file_hash`, file-level idempotency) → parsing
(TXT/MD/CSV/PDF/DOCX/XLSX; failures are flagged, never fatal) → normalization
(NFC, CRLF, conservative whitespace trimming) → canonical JSON hash
(`content_hash`) → greedy ~512-token chunking (overlapping windows for oversized
content; `chunk_index` is stable for identical content).

**Version chains accept explicit declarations only**: documents are linked only
when the config says `supersedes`; links are never inferred by sorting version
labels (`v10` will not attach to `v2`). Missing or ambiguous references only
produce warnings.

### 4.2 Extraction

- LLM output is constrained by a versioned schema (`fact-extract-v4`), with a
  three-level fallback: strict `json_schema` → JSON mode → prompt-only.
  **Outputs at every level are re-validated by our own Pydantic models and the
  deterministic `FactClaim`/`Evidence` checks**;
- Entity resolution order: exact normalized name → alias → globally unique
  match. Cross-type ambiguity is rejected rather than guessed. When a small
  model forgets to declare an entity, an undeclared reference that resolves
  uniquely is deterministically salvaged as an `unknown`-type entity with a
  warning (the evidence requirement and ambiguity rejection are never relaxed).
  **R8 (v0.1.1)**: same-name entities whose declared types drift only within the
  controlled "sellable-offering" class (product↔service and similar synonyms,
  and only when the name is unique) are deterministically merged into one entity;
  merging across semantic categories (person/org/policy/unknown…) never happens;
- **Evidence First**: every fact has at least one piece of evidence (document,
  chunk, page, verbatim quote, source type, authority). If the model's quote is
  not found verbatim in the source, the evidence is re-anchored to the chunk
  text and a warning is raised;
- At the end of a scan an immutable snapshot is appended and a **deterministic
  Knowledge Hash** is computed: each fact is projected to canonical JSON with
  sorted keys (excluding ids/timestamps/embeddings/confidence), the collection
  is sorted, then SHA-256 is applied — independent of insertion order, so
  unchanged content always hashes identically.

### 4.3 Detection

`KnowledgeState` loads only active facts (date scalars in JSONB are restored
from ISO strings). The four detectors each emit `DriftCandidate` objects:

| Detector | Decision essentials |
|---|---|
| Conflict | Group by (subject, predicate); skip identical values (multi-evidence Canonical Facts), non-overlapping windows, same-document rows (tier tables), documents with an explicit newer version, and predicates configured as multi-valued |
| Stale | Expired `valid_to` or a superseding source → **confirmed** (0.95); age only beyond threshold (90 days pricing / 365 otherwise) → **possibly** (0.6); **never flagged without an age signal** |
| Superseded | Document-level; follows only the explicit `previous_version_id` chain; n versions yield n−1 findings |
| Duplicate | Names/embeddings **recall only**; confirmation requires same predicate + overlapping windows + canonical object equality. A single shared fact must additionally be identifying (held by exactly these two entities in the whole KB); generic attributes such as billing units never justify a merge |

Confidence bands: structural evidence 0.95 / recall-based 0.90 / age heuristics
0.60. Severity and AI-impact level are independent concepts stored separately.

The orchestration service computes a **deterministic fingerprint** for each
candidate (SHA-256 over type + predicate + old/new fact ids, etc.) and compares
it against fingerprints of all historical drift findings (including
ignored/resolved ones) — only genuinely new findings are persisted. The
`detector_version` (currently `drift-core-v1`) is recorded on the scan run so
rule evolution stays traceable.

### 4.4 Resolution

Drifts move through three states — `open → ignored / resolved` — managed by
`ResolutionService`:

- **Four decisions**: `accept_newer`, `keep_old`, `manual_override`,
  `false_positive`;
- **Controlled reason codes** (the 10-value `ReasonCode` enum) coexist with a
  free-text reason: codes feed future statistics, text serves humans;
- **Authority-fact rules**: accept_newer/keep_old default to the new/old fact
  and can be overridden explicitly, but the override must be one of the drift's
  own old/new facts. Document-level drifts (superseded) have no facts and
  reject an authority id; manual/false_positive produce no authority fact;
- **One-shot, immutable**: `resolutions.drift_id` is UNIQUE; a resolved drift
  can neither be re-resolved nor ignored (correcting a mistake requires a
  data-layer fix — the audit trail is never silently overwritten). Ignoring is
  idempotent, but a resolved drift can never become ignored;
- Phase 0 supports `scope=single` and `pattern_jsonb=NULL` only (no bulk
  pattern-based resolution);
- The ignore reason is stored in `drifts.detail_jsonb["ignore_reason"]`; the
  status stays on the drift row.

The point of resolution is the **Remember loop**: fingerprint dedup compares
against all history, including resolved/ignored findings, so handled issues
always appear as already known on later scans and the CI gate counts only open
drifts.

### 4.5 Reporting

Terminal output, JSON files and HTML files share one Pydantic DTO
(`ReportDTO`); no renderer may query the database or embed rules of its own:

```text
ReportBuilder (queries the DB and assembles the DTO — the only report
   │           component touching the ORM)
   └─▶ ReportDTO(summary, issues)
          ├─▶ render_json   deterministic (sort_keys, ensure_ascii=False)
          └─▶ render_html   Jinja2 template (autoescape explicitly includes .j2)
```

- **Each issue**: the five elements (title, why it is a problem, recommended
  action, suggested decisions — with type-specific Chinese narratives) plus
  old/new fact snippets, old/new sources, deduplicated verbatim evidence and
  the resolution record. Dangling references (facts/documents deleted later,
  FK SET NULL) never crash the report;
- **Summary**: document/entity/fact counts, new vs suppressed this scan, the
  three status counts, distributions by type and severity, and the knowledge
  hash used by the scan;
- **CI badge**: a pure-function policy (`severity ≥ fail_on`, threshold
  inclusive; `none` always passes) feeds the DTO and maps to exit code `1`
  in the CLI;
- HTML is a single self-contained file with inline CSS and zero external
  dependencies — ready to archive as a CI artifact;
- `check` renders the state persisted by the latest scan (no LLM calls, no
  token spend); `scan` writes report files after the transaction commits, so a
  report always reflects committed state.

### 4.6 Evaluation

Evaluation is a **side-car quality layer** that answers "how accurately do the
detectors adjudicate?" — it does not re-run a scan:

```text
examples/qa_cases/*.yaml
   └─▶ QACase (documents / entities / facts + rule thresholds + expected drifts)
          └─▶ build_world   constructs KnowledgeState / DetectionContext directly
                 │            (ids derived via uuid5 from case id + local id; reproducible)
                 └─▶ default_detectors().detect(...)   ← the real detectors
                        └─▶ match_findings   expectations ↔ candidates (type required,
                               └─▶ compute_metrics     other fields narrow the match)
                                                      P / R / F1 / FPR + Gate
```

- **No external dependencies**: detectors already consume the frozen
  `KnowledgeState` dataclass, so a case needs no database and no LLM and runs in
  milliseconds. This evaluates exactly the "deterministic adjudication core"
  drawn by #22/#23 (LLMs only propose; embeddings only recall), and anyone can
  re-run it after cloning;
- **Three categories**: a positive case must declare ≥ 1 expected drift;
  negative / ambiguous-negative cases must expect zero. The invariant is
  enforced in the schema so a mistyped case can never game the numbers;
- **Metric definitions**: finding-level Precision / Recall / F1 treat each
  expected drift as a labelled positive (TP/FP/FN). FPR is measured at *case*
  level over benign scenarios (negative + ambiguous): false-alarm cases / benign
  cases. The two definitions are reported transparently and never conflated;
- **Strict Gate inequalities**: P>80% / R>70% / F1>75% / FPR<30%, and both
  positive and benign sets must be non-empty (no empty-set gaming). Per-detector
  P/R and a false-positive breakdown are also emitted;
- The real-world **HCR (Human Confirmation Rate)** is an in-production metric and
  is deliberately kept out of the offline Gate; the README states the boundary so
  a high offline score is never presented as proof of production performance.

`truthlayer eval` is a thin shell: load cases → run the harness → compute metrics
→ terminal/JSON/Markdown, with exit codes `0` Gate passed / `1` Gate failed /
`2` cases could not load or an artifact could not be written.

## 5. Configuration and credentials

- Every `.truthlayer.yaml` field is strongly validated by Pydantic
  (`extra="forbid"` — unknown keys fail outright): source authority, staleness
  thresholds, severity overrides, CI fail_on, explicit version chains, and the
  two endpoint slots for extraction/embedding;
- **Credentials come only from environment variables**: config files may name
  the variable via `api_key_env`, never contain a key;
- LLM and embedding endpoints may be the same provider (one key), two providers
  (two keys), or local Ollama (zero keys). The embedding slot is optional —
  missing vectors never block knowledge ingestion. Vector columns are not
  dimension-locked; the actual dimension is recorded on the scan run.

## 6. Error handling

The domain layer defines semantic error categories (config, parsing, provider,
database, domain validation, user input, …). CLI exit codes are a stable
contract: `0` success; `1` fail_on gate failure (open drifts meet the threshold;
ignored/resolved findings never block); `2` system error (invalid config,
unreachable database, files that failed to parse, report write failure). System
errors outrank the gate: `2 > 1 > 0`. Failed scans are also recorded in
`scan_runs` (status=failed, error_message).

## 7. Test architecture

- **Unit tests** (`tests/unit/`, no database): mostly pure functions and
  detectors. Detector tests build in-memory frozen-state fixtures and
  deliberately cover negative cases (same-document tiers, `149 == 149.0`,
  non-overlapping windows, generic-attribute false merges, …);
- **Integration tests** (`tests/integration/`): each session uses a throwaway
  database `<dbname>_it`, automatically running `alembic upgrade head` and
  `downgrade base`. Rows are constructed directly without calling an LLM,
  covering detection, persisted fields and cross-scan dedup for all five drift
  types, the full Resolution lifecycle (including double-resolve guards and
  authority-fact rules), report assembly and the drift/resolve CLI commands;
- **Ollama smoke test**: skipped by default; with `TRUTHLAYER_RUN_OLLAMA=1`
  it runs end-to-end against real models, so CI without Ollama is unaffected;
- **Golden QA regression** (`tests/unit/evaluation/`): loads every case under
  `examples/qa_cases`, runs the real detectors on pure in-memory state, and
  asserts the size range (50–100), all three categories, a positive for every
  drift type, and that the suite keeps meeting the Phase 0 Gate. No database or
  LLM — it runs with the ordinary unit tests.

## 8. Key design decisions (required reading for contributors)

1. **Evidence First**: no fact may exist without verbatim, locatable evidence;
2. **LLMs only propose**: model output is always candidate input; deterministic
   rules decide admission;
3. **Embeddings only recall, never adjudicate**: no drift conclusion may derive
   solely from vector similarity;
4. **Determinism first**: knowledge hashes, drift fingerprints, ordering and
   chunk indices must not depend on randomness or set iteration order, and
   results are timezone-independent (UTC throughout);
5. **Explicit over guessing**: version chains come only from config; ambiguity
   in parsing or entity resolution defaults to rejection or warnings;
6. **The CLI is a thin shell**: business rules live only in the domain/service
   layers, ready for direct API reuse;
7. **Resolution is audit**: decisions are one-shot and immutable, reason codes
   are controlled, free-text reasons are retained, and resolution never
   deletes evidence;
8. **One report model**: terminal/JSON/HTML render the same DTO; renderers
   neither adjudicate nor query the database;
9. **Cases before rules**: add business-semantics positive/negative/ambiguous QA
   cases before changing a detector; the offline Gate measures only the
   deterministic core, and production HCR is collected separately — neither is
   used to dress up the other.
