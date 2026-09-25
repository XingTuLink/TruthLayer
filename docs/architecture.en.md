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
│  ingestion service → extraction service → detection service │
├──────────┬──────────────────┬──────────────────┬────────────┤
│ ingestion│   extraction     │    detection     │ providers  │
│ parse/   │  LLM extraction/ │  state views /   │ OpenAI-    │
│ chunk    │  entity resolve  │  detectors       │ compatible │
│ versions │  evidence/hash/  │  fingerprints /  │ LLM/Embed  │
│          │  snapshots       │  orchestration   │            │
├──────────┴──────────────────┴──────────────────┴────────────┤
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
| `truthlayer.providers` | OpenAI-compatible LLM/Embedder implementations (cloud, gateways, Ollama) | Protocol-based and replaceable |
| `truthlayer.cli` | Typer commands, transaction commit, terminal output | Thin shell; no business rules allowed |

### Two transaction disciplines

1. **Services only `flush`, never `commit`**: a scan is one transaction spanning
   ingestion → extraction → detection. The CLI commits once after everything
   succeeds; any failure rolls the whole run back.
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
├── resolutions         （Sprint 5)
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

- LLM output is constrained by a versioned schema (`fact-extract-v1`), with a
  three-level fallback: strict `json_schema` → JSON mode → prompt-only.
  **Outputs at every level are re-validated by our own Pydantic models and the
  deterministic `FactClaim`/`Evidence` checks**;
- Entity resolution order: exact normalized name → alias → globally unique
  match. Cross-type ambiguity is rejected rather than guessed. When a small
  model forgets to declare an entity, an undeclared reference that resolves
  uniquely is deterministically salvaged as an `unknown`-type entity with a
  warning (the evidence requirement and ambiguity rejection are never relaxed);
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

The domain layer defines eight semantic error categories (config, parsing,
provider, database, domain validation, …). CLI exit codes: `0` success;
`2` system error (invalid config, unreachable database, files that failed to
parse); the fail_on threshold code `1` arrives in Sprint 5. Failed scans are
also recorded in `scan_runs` (status=failed, error_message).

## 7. Test architecture

- **Unit tests** (`tests/unit/`, no database): mostly pure functions and
  detectors. Detector tests build in-memory frozen-state fixtures and
  deliberately cover negative cases (same-document tiers, `149 == 149.0`,
  non-overlapping windows, generic-attribute false merges, …);
- **Integration tests** (`tests/integration/`): each session uses a throwaway
  database `<dbname>_it`, automatically running `alembic upgrade head` and
  `downgrade base`. Rows are constructed directly without calling an LLM,
  covering detection, persisted fields and cross-scan dedup for all five drift
  types;
- **Ollama smoke test**: skipped by default; with `TRUTHLAYER_RUN_OLLAMA=1`
  it runs end-to-end against real models, so CI without Ollama is unaffected.

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
   layers, ready for direct API reuse.
