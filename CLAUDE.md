# CLAUDE.md

Project: **Mission-Aligned Careers** (repo `mission-aligned-careers`, package `job_radar`, CLI `radar`). A config-driven
Python pipeline whose key mission is to **discover companies and roles the
user would not find on their own**, score their fit against the user's
profile, watch career boards and flag open-application candidates.

Read `docs/ARCHITECTURE.md` before any structural change. It is the source of
truth for design, data model, open decisions and roadmap.

Configuration is split in two:
- `packs/<pack>/taxonomy.yaml` — shareable vocabulary (themes, sectors, org
  types, role families...). The first pack is `climate`.
- `config/criteria.yaml` — the user's personal preferences (gitignored);
  `config/criteria.example.yaml` is the committed example.

## Stack
- Python 3.12+, managed with **uv** (`uv sync`, `uv run ...`). On this Mac uv
  lives in `~/Library/Python/3.13/bin`; it must be on PATH for pre-commit.
- pydantic v2 for all domain models and config
- httpx for HTTP; respx for mocking it in tests
- DuckDB for storage (local file in `data/`); SQL views for analytics
- LLM extraction behind the `LLMExtractor` protocol (route still open, see ARCHITECTURE §9)
- typer for the CLI; Markdown digest first, Streamlit review app later
- pytest, ruff (lint + format), mypy --strict, pre-commit

## Commands
- Install: `uv sync`
- Tests: `uv run pytest`
- Lint/format: `uv run ruff check . && uv run ruff format .`
- Types: `uv run mypy src`
- Validate config: `uv run radar profile check`

## How we work
- **Pair closely.** Pause after each failing test and at each design decision
  for the user's input. Explain choices in plain terms.
- **Test-driven.** For each behaviour: write a failing test, make it pass,
  refactor. Show the failing test before writing the implementation.
- **Small steps, one roadmap step at a time.** Use plan mode for anything
  touching more than a couple of files.
- Commit after each green step with a conventional commit message.
- Before saying a task is done: tests, ruff and mypy all pass.
- Every public function and class has a docstring saying what it does and why
  (`docs/CODE_TOUR.md` maps the code; keep it current when files move).
- Budget: free sources only for now; no paid API calls without asking.

## Design rules
- The domain core (`domain/`, `scoring/`, `profile/`) must not import httpx,
  duckdb, anthropic or any other I/O library.
- **No domain specifics in code.** Themes, sectors, org types, role families
  and cities come from the pack and criteria. Only generic vocabularies
  (contract, remote, seniority, salary period) are enums.
- Every external system sits behind an ABC or Protocol, with a fake
  implementation for tests. Inject dependencies through constructors; no
  module-level singletons for external systems.
- New job board clients and sources register via their registry decorator.
- **The LLM extracts facts; plain code computes scores.** Never put scoring
  logic in prompts. The LLM sees the pack, never the user's preferences.
- Prompts live in `enrich/prompts/` and are versioned. The version is stored
  with every extraction.
- Scoring weights and thresholds come from criteria, never hard-coded.
- Stable IDs (`company_id` = registered domain, `job_id` = `ats:slug:native_id`),
  idempotent runs, record `first_seen` / `last_seen`.
- Short-lived DuckDB connections (one writer process at a time).

## Testing rules
- Unit tests never touch the network or a real LLM. Use recorded fixtures in
  `tests/fixtures/` and fake implementations.
- When adding an ATS client or source: first record a real public response
  into `tests/fixtures/`, then write the parsing test against it.
- **Scrub personal data before committing a sample**: trim it to what the test reads, replace
  people's names / personal emails, drop personal LinkedIn links and photos; keep the original
  in `local/` (gitignored). See `tests/fixtures/README.md`.
- Calibration companies in the criteria become scorer test cases.
- A test loads the shipped example criteria with the shipped pack, so the two
  never drift apart.

## Web etiquette
- Respect robots.txt and site terms, rate-limit per host (default 1 request/s),
  send a descriptive User-Agent, and cache responses.
- Prefer official public JSON feeds and APIs over HTML scraping.

## Privacy
- The user's CV, criteria, company list and DuckDB file are personal. Never
  commit `data/`, `config/criteria.yaml`, `config/companies.csv` or
  `config/cv.*`. Keep `.gitignore` up to date.
- Secrets come only from environment variables.
