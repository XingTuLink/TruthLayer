# Contributing to TruthLayer

> 🌐 Language: [简体中文](./CONTRIBUTING.md) · **English**

Thanks for your interest in TruthLayer! This guide explains how to set up a
development environment, the project's engineering principles, and the
Issue/PR workflow.

## Code of conduct

By participating you agree to stay friendly and professional: respect
contributors of all backgrounds, debate ideas rather than people, and never
engage in harassment or personal attacks.

## 1. Development environment

Requirements: Python 3.11+, PostgreSQL 15+ with
[pgvector](https://github.com/pgvector/pgvector).

```powershell
git clone git@github.com:XingTuLink/TruthLayer.git
cd TruthLayer

python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\python -m pip install -e ".[dev,parsers,openai]"
# macOS / Linux
# .venv/bin/pip install -e ".[dev,parsers,openai]"
```

Initialize a local development database:

```powershell
$env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://user:password@localhost:5432/truthlayer"
.\.venv\Scripts\python scripts/dev_init_db.py
.\.venv\Scripts\alembic upgrade head
```

> Installing pgvector: use your distro package on Linux (e.g.
> `postgresql-16-pgvector`), Homebrew on macOS; on Windows follow the official
> pgvector build instructions with the Visual Studio build tools.

For a keyless end-to-end run, install [Ollama](https://ollama.com/), pull the
models with `ollama pull qwen2.5:7b bge-m3`, and run
`.\.venv\Scripts\truthlayer scan .\examples\demo_kb`.

## 2. Running the tests

```powershell
# Unit tests: no database needed; every PR must keep these green
.\.venv\Scripts\python -m pytest tests/unit -q

# Full suite: integration tests create/drop a throwaway <dbname>_it database
$env:TRUTHLAYER_DATABASE_URL="postgresql+psycopg://..."
.\.venv\Scripts\python -m pytest -q
```

## 3. Code layout

- `src/truthlayer/domain/`: pure domain rules — no ORM, CLI, or vendor SDK imports;
- `src/truthlayer/ingestion|extraction|detection/`: capability packages;
  services only `flush`, never `commit`;
- `src/truthlayer/cli/`: thin Typer shell — argument parsing, transaction
  control and output only;
- `tests/unit/` and `tests/integration/` are separate: anything that does not
  need a database goes into unit.

Schema changes must ship with an Alembic migration that runs cleanly in both
directions (`alembic upgrade head` and `downgrade base`).

## 4. Design red lines (non-negotiable)

These principles define the project; PRs violating them will not be merged:

1. **Evidence First**: every fact must be anchored to locatable verbatim evidence;
2. **LLMs only propose**: model output must pass deterministic validation before
   persistence; models never make final decisions;
3. **Embeddings only recall, never adjudicate**: no drift conclusion may rest
   solely on vector similarity;
4. **Deterministic and reproducible**: hashes, fingerprints, ordering and chunk
   indices must not depend on randomness or set iteration order; local timezones
   must not affect results (everything is handled in UTC);
5. **Explicit over guessing**: version chains come only from config; ambiguity
   in parsing or entity resolution defaults to rejection or warnings;
6. **Zero business logic in the CLI**: rules live only in the domain/service
   layers so a future API can reuse them directly;
7. **Credentials via environment variables only**: `.truthlayer.yaml` may only
   contain an `api_key_env` variable name. Never commit real keys, `.env`
   files, or real enterprise data.

## 5. Coding style

- Python 3.11+ with type annotations throughout; public functions get clear
  docstrings;
- Prefer plain, direct naming; errors must use the semantic error types in
  `truthlayer.domain.errors`;
- Write/update tests for every behavior change: bug fixes include a regression
  test that reproduces the issue;
- New detector branches need both **positive and negative cases** (especially
  "should not fire" scenarios);
- Do not add unnecessary dependencies; prefer the standard library.

## 6. Commit messages

Use concise imperative sentences, ideally with a module prefix:

```text
detection: tighten the single-fact identifying condition for duplicates
ingestion: fix integers rendered as floats in XLSX output
docs(readme): document the Ollama setup
test(stale): add negative case for facts without an age signal
```

Keep one PR focused on one topic; avoid mixing in unrelated refactoring.

## 7. Issue and PR workflow

**Issues:**

- Bugs: include the reproduction command, expected vs. actual behavior,
  Python/PostgreSQL versions, and the full error output;
- Feature proposals: explain the problem and a typical use case; no need to
  design the implementation in advance.

**Pull requests:**

1. Branch from the latest main and give the PR a descriptive title;
2. Describe what changed, why, and how it was verified; link related issues;
3. Make sure `pytest -q` is green; for database changes verify the migration
   upgrades and downgrades locally;
4. Update README or [docs/architecture.en.md](./docs/architecture.en.md) for
   user-visible behavior changes;
5. Push review follow-ups to the same branch; please do not force-push to
   rewrite history.

## 8. License

By opening a PR you agree that your contribution is licensed to the project
under [AGPL-3.0](./LICENSE), and that you have the right to grant this license.

## 9. Security

Please do not report security vulnerabilities or post credentials in public
issues. Report security concerns privately to the repository maintainers; we
will respond as quickly as possible.
