# Code tour

A map of the code for reading it the first time: what each package does, then three walks
through real commands, naming the functions and tables involved. Every file starts with a
docstring saying what it is for; every public function and class has one saying what it does
and why.

## The shape of it

```
                ┌───────── outer layers: how you start things ──────────┐
                 cli.py (radar …)   orchestration/ (Dagster)   api/ (FastAPI)
                └──────────────────────────┬─────────────────────────────┘
                                           ▼
                pipeline/   the use cases: add, discover, roles, find-careers, demo
                    │
      ┌─────────────┼────────────────┬──────────────────┬───────────────────┐
      ▼             ▼                ▼                  ▼                   ▼
   sources/      boards/          enrich/        scoring/ matching/      notify/
   lists, CSVs   job boards       websites,      scores, decisions,      reports,
   pasted text   (7 feeds)        facts, models  role lists, CV fit      CSV
      └─────────────┴───────┬────────┴──────────────────┴───────────────────┘
                            ▼
          web/ (polite HTTP)          storage/ (Repository → DuckDB)
                            ▲
          domain/ (Company, JobPosting, CompanyFacts)   profile/ (pack + criteria)
```

Rules that shape the code (see `CLAUDE.md`):
- **The core has no I/O.** `domain/`, `scoring/` and `profile/` never import HTTP or database
  libraries, so they are pure and easy to test.
- **Everything external sits behind an interface** with a fake for tests: `HttpClient`
  (web/http.py), `Repository` (storage/repository.py), `LLMExtractor` / `NameExtractor` /
  `WebsiteResolver` (enrich/), `JobBoardClient` (boards/registry.py).
- **A model only extracts facts; plain code scores.** No preferences are sent to a model.
- **Nothing domain-specific is hard-coded**: themes, sectors, role families and skills come
  from the pack (`packs/climate/taxonomy.yaml`), preferences from your criteria.

## Packages and files

| Package | What it does | Main files |
|---|---|---|
| `domain/` | the core records | `models.py` (Company, JobPosting, contract / remote / seniority vocabularies), `facts.py` (CompanyFacts), `ids.py` (company_id = registered domain, job_id) |
| `profile/` | pack + criteria, validated | `models.py` (every YAML section as a typed model), `loader.py` (load and cross-check) |
| `settings.py` | where files live | `RadarPaths` (defaults + `JOB_RADAR_*` overrides), `read_cv` |
| `web/` | fetching pages | `http.py` (robots.txt, 1 req/s per site, retries, cache), `replay.py` (demo's recorded web) |
| `sources/` | where companies come from | `list_page.py` (any list page, list registry), `norrsken.py`, `sitemap_profiles.py`, `csv_source.py` (your list + pending), `added.py` (`radar add` inputs), `decisions_csv.py` |
| `boards/` | where jobs come from | `classify.py` (what kind of careers link), `detect.py` (board inside a careers page), `registry.py` (reader registry, feed helpers), one file per board (Ashby, Greenhouse, Workable, Teamtailor, Personio, BambooHR, Recruitee), `jsonld.py`, `parsing.py` |
| `enrich/` | learning about companies | `website.py` (read homepage + about page), `resolve.py` (name → website, verification), `extractor.py` (facts with Claude), `keywords.py` (facts without a model), `replay.py` (record / replay answers), `select.py` (which route), `careers_guess.py`, `usage.py`, `prompts/` (versioned prompt) |
| `scoring/` | turning facts into decisions | `company.py` (impact / practical / career / overall, with explanations), `decide.py` (follow / worth a look / rejected) |
| `matching/` | roles | `prefilter.py` (text normalisation, role family, location), `roles.py` (to watch / to review / rejected), `cv.py` (skills and CV fit) |
| `watch/` | plain careers pages | `page_text.py` (visible lines of a page) |
| `pipeline/` | the use cases | `watch.py` (import your list, watch boards), `discover.py`, `add.py`, `roles.py`, `find_careers.py`, `demo.py`, `doctor.py` |
| `notify/` | outputs | `discover_report.py`, `roles_report.py` (Markdown), `scores_csv.py` |
| `storage/` | the database | `repository.py` (the interface), `duckdb_repository.py`, `migrations/` (numbered SQL) |
| `orchestration/` | Dagster | `assets.py`, `checks.py`, `resources.py`, `definitions.py` |
| `api/` | FastAPI | `app.py` (endpoints + dashboard), `schemas.py`, `runs.py`, `templates/dashboard.html` |
| `cli.py` | the `radar` command | one function per command, each calling a pipeline |

## Database tables

| Table | Holds |
|---|---|
| `companies`, `company_sources` | every company, and each list / input it was found on |
| `pending_companies` | companies waiting for your input |
| `company_texts`, `website_status` | text read from websites; whether reading worked and why not |
| `company_facts` | fact sheets, keyed by extraction version + text hash (the model cache) |
| `fit_assessments` | each company's latest scores and decision |
| `career_boards` | where a followed company's jobs are read from, and the last check |
| `job_postings` | every posting seen, with first / last seen and closed date |
| `page_snapshots` | visible lines of plain careers pages |
| `careers_found`, `careers_missing` | careers pages found by trying addresses; companies flagged as having none |
| `list_reads` | when each list was last read |
| `pipeline_runs`, `llm_calls` | every run (status, counts, problems) and every model call |

## Walk 1 — `radar add --text "Met the Kanop team at ChangeNOW"`

1. `cli.py: add_command` loads your profile, opens the database, imports your list
   (`pipeline/watch.py: run_import`).
2. `enrich/select.py: select_add_routes` picks the model routes: Claude Code if installed,
   otherwise the free ones.
3. The text becomes candidates: `ClaudeCodeNameExtractor.extract` (or `LineParser`).
4. `pipeline/add.py: run_add`, for each company:
   - funds, accelerators, events → suggested lists, not scored;
   - website: given, or found by `ClaudeCodeResolver` (web search), then checked by
     `verify_website` (plain code); unresolved → `pending_companies`;
   - stored with `upsert_companies` (`companies`, `company_sources`).
5. `pipeline/discover.py: assess_companies` reads each website (`WebsiteReader`), extracts
   facts (`company_facts`), scores (`scoring/company.py: score_company`), decides
   (`scoring/decide.py: decide`) and saves (`fit_assessments`); followed companies get a board
   (`career_boards`).
6. `notify/discover_report.py` writes `data/digests/added-<date>.md`.

## Walk 2 — the weekly run (`radar roles`, or Dagster's `weekly_radar`)

1. `pipeline/roles.py: run_roles` starts a run (`pipeline_runs`).
2. `pipeline/watch.py: watch_boards` takes each followed company's board that is due (7 days):
   - a job board → its reader (`boards/<name>.py`) → `sync_postings` adds new postings,
     updates known ones and closes those gone (`job_postings`);
   - a plain careers page → `detect_board` looks for an embedded board; if none, the page's
     lines are kept (`page_snapshots`);
   - anything unreadable → "check by hand", with the reason.
3. `build_roles_report` sorts every open role (`matching/roles.py: classify_role`), adds the CV
   fit (`matching/cv.py`), careers-page mentions and the pending list.
4. `notify/roles_report.py` writes `roles-<date>.md`. In Dagster the same functions run as the
   `open_roles` asset, after `company_scores` (discovery) and before checks.

## Walk 3 — opening the dashboard (http://localhost:8000/)

1. Uvicorn passes the request to `api/app.py: dashboard` (an endpoint inside `create_app`).
2. It reads only — nothing is fetched or scored: `repo.assessments()` (companies),
   `build_roles_report` (roles + CV fit, CV read from `config/cv.md`), `pipeline_runs` (last
   updated).
3. `api/templates/dashboard.html` renders the tables. To refresh the data, a pipeline must run
   first (CLI, Dagster, or `POST /runs/weekly_radar`).

## Tests

`tests/unit` (pure functions), `tests/contract` (board readers against real recorded
responses in `tests/fixtures/`), `tests/integration` (pipelines end to end with fake HTTP and
an in-memory database). `./scripts/ci.sh` runs everything CI runs.
