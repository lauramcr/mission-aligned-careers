# Architecture — Mission-Aligned Careers

A personal, configurable pipeline whose **key mission is discovery**: surface
companies and roles the user would not have found, or had time to find, on
their own. It also scores how well companies fit the user's profile, watches
their career boards for matching roles, and flags strong companies for open
(cold) applications.

The tool ships **an engine, domain packs and sources, not data**. Each user
picks a pack (the first one is `climate`), writes personal criteria, enables
the discovery sources they want, and builds their own local database.

---

## 1. Configuration: domain pack + personal criteria

| File | Committed? | Contains |
|---|---|---|
| `packs/<pack>/taxonomy.yaml` | yes (shareable) | **Vocabulary**: themes (with descriptions for the LLM prompt), sectors, organisation types, business models, role families (with EN/FR title keywords), flag texts |
| `config/criteria.example.yaml` | yes | A complete example of criteria |
| `config/criteria.yaml` | **no** (gitignored) | The user's **preferences**: theme tiers and bonuses, excluded sectors, penalties, practical/career scoring values, target roles, contracts, office-days curve, weights, thresholds, calibration companies |

Criteria may only reference ids defined in the pack; `radar profile check`
reports every problem with its YAML location. The LLM sees the pack, never the
preferences, so editing preferences never requires re-extraction.

---

## 2. Pipelines

| Pipeline | Cadence | Input | Output |
|---|---|---|---|
| **Discover** | weekly (roles) / monthly (companies) | enabled sources: market-wide job sources, directories, portfolios, the user's own CSV | new rows in `companies`, `company_sources`, `job_postings` |
| **Enrich** | after discover | company website text | versioned `company_facts` (LLM-extracted) |
| **Score** | after enrich, or whenever criteria change | facts + profile | `fit_assessments` (3 sub-scores + overall + explanation) |
| **Review** | user, ad hoc | scored companies | `decisions` (follow / reject / open application) |
| **Watch** | weekly | followed/known companies' career boards | `job_postings` (first_seen / last_seen / is_open) |
| **Match** | after watch | new postings + profile (+ CV later) | `job_matches` (role_fit + reasons) |
| **Notify** | after match | new matches, new-to-you companies, open-application candidates | weekly Markdown digest |

Two discovery channels feed each other: **role-led** (search the market for
roles; each role reveals a company) and **company-led** (directories and
portfolios; then watch their boards). Every newly found company goes through
enrich → score → review. Companies from the user's own list are **known**;
everything else is **new to you**, and the digest leads with it.

Every run writes a row to `pipeline_runs` (counts, errors, duration).

**Cadence and freshness.** Each step runs only when due, so re-running is cheap and
idempotent; `--force` options override.

| Step | Bookkeeping | Due when |
|---|---|---|
| Job check per followed company (`radar roles`, Dagster `weekly_radar`) | `career_boards.last_checked_at`; roles keep `first_seen_at` and `found_run_id` | never checked, or ≥ 7 days (`--max-age-days`) |
| Website text + facts (`radar discover`) | `company_texts.fetched_at`; facts keyed by content hash + extraction version | ≥ 30 days, or the text changed; a site that failed is retried after 7 days (`website_status`) |
| List pages (`radar discover`) | `list_reads.last_read_at` | ≥ 30 days (`--force-lists`) |
| Similar-company search (planned) | per run | monthly, plus a manual "search again" |
| Scores | recomputed every run | always (free) |

Role reports compare with the previous report: **new since**, **still open** (with the date
found) and **closed since**.

---

## 3. Design principles

1. **The LLM extracts; code decides.** The LLM turns web pages into a strict,
   validated schema. All scoring is deterministic Python driven by the
   criteria: reproducible, explainable, unit-testable, free to re-run.
2. **Generic engine, domain in config.** No theme, sector or city is
   hard-coded. Only generic vocabulary lives in code (contracts, remote
   policy, seniority, salary period).
3. **Ports and adapters.** The domain core has no knowledge of HTTP, DuckDB or
   the LLM. Each external system sits behind an interface with a fake for tests.
4. **Registries for plug-ins.** Job board clients, sources and notifiers
   register by name.
5. **Idempotent, incremental runs.** Stable IDs, content hashes for pages,
   versioned prompts and profiles for assessments.
6. **Cheap filters before expensive calls.** Plain-code prefilters (title
   keywords, contract, location) run before any LLM call.
7. **Good citizenship.** robots.txt, per-host rate limits, descriptive
   User-Agent, caching, official feeds over scraping.

---

## 4. Package layout

Modules are created when their milestone starts.

```
src/job_radar/
  domain/        # ids.py (company_id, job_id), models.py (Company, CareerBoard, JobPosting, enums)
  profile/       # models.py (Taxonomy, Criteria, Profile), loader.py (validation)
  sources/       # CompanySource / JobSource ABCs + registry → CsvSource, FranceTravail, directories...
  boards/        # JobBoardClient ABC + registry → Ashby, Greenhouse, Workable, ...; detect.py
  web/           # HttpClient: rate limiting, robots.txt, caching, retries
  enrich/        # LLMExtractor Protocol → adapters (route TBD, see §9); prompts/ versioned
  scoring/       # CompanyScorer, RoleScorer, explanations
  geo/           # CommuteCalculator Protocol → transit routing, haversine fallback
  storage/       # Repository Protocol → DuckDBRepository; migrations; SQL views
  notify/        # Markdown reports (discovery, roles) and the scores CSV
  pipeline/      # use cases: discover, enrich, score, watch, match, notify
  cli.py         # typer: `radar ...`
packs/climate/   # domain pack
config/          # criteria.example.yaml (+ gitignored criteria.yaml, companies.csv, cv.*)
tests/{unit,contract,integration,fixtures}/
```

---

## 5. Data model (DuckDB, local file in `data/`)

**Identifiers**

- `company_id` = registered domain of the website (`jobs.acme.com` → `acme.com`),
  using the Public Suffix List bundled with `tldextract` (offline), so
  `acme.co.uk`, `economie.gouv.fr` and `acme.github.io` stay distinct.
  Different domains for one company (`acme.fr` vs `acme.com`, rebrands) will be
  handled by an alias table when discovery sources arrive.
- `job_id` = `{ats}:{slug}:{native_id}`. To revisit when role-led sources
  without an ATS (e.g. France Travail) are added.

**Core tables**

- `companies` — company_id, name, website, country, city, first_seen_at, last_seen_at
- `company_sources` — company_id, source_name, source_url, discovered_at
  (a company is *known* when it came from the user's own list)
- `company_pages` — company_id, url, content_hash, text, fetched_at
- `company_facts` — company_id, extraction_version (prompt + route + model),
  content_hash, short_description, primary_theme, secondary_themes[],
  org_type, headcount_estimate, funding_status, office_locations[],
  core_sectors[], major_client_sectors[], data_centrality, business_model,
  confidence, evidence (JSON), extracted_at
- `fit_assessments` — company_id, facts_id, profile_hash, impact_fit,
  practical_fit, career_fit, overall, flags[], explanation (JSON), scored_at
- `decisions` — company_id, verdict (follow / reject / open_application), note, decided_at
- `career_boards` — company_id, ats, slug, careers_url, detected_at, last_ok_at
- `job_postings` — job_id, company_id, source, title, url, location, remote,
  contract, part_time, office_days, seniority, department, language,
  salary_min, salary_max, currency, salary_period, description,
  published_at, first_seen_at, last_seen_at, is_open
- `job_matches` — job_id, profile_hash, cv_hash, role_fit, commute_minutes,
  reasons (JSON), excluded_reason, is_cold_application_signal, matched_at
- `pipeline_runs` — run_id, pipeline, started_at, finished_at, status, counts (JSON), errors (JSON)

**Generic vocabularies (in code)**

- contract: `permanent | fixed_term | freelance | internship | apprenticeship | unknown`,
  parsed from FR/EN labels (CDI, CDD, stage, alternance, ...); part-time is a
  separate flag.
- remote: `onsite | hybrid | remote | unknown`
- seniority: `junior | mid | senior | lead | executive | unknown`
- salary period: `year | month | day | hour`

**Views**: `v_company_overview`, `v_review_queue`, `v_new_to_you`,
`v_open_application_candidates`, `v_new_jobs_this_week`,
`v_discovery_funnel` (found → scored → followed → applied, per source),
`v_scoring_agreement`, `v_pipeline_health`.

**Concurrency:** DuckDB allows one writer process. Every component opens a
short-lived connection per action and closes it, so the review app and a
scheduled run don't lock each other out. Long CLI runs (discover, roles, add,
weekly_radar) hold one connection for the duration of the command
(`repo.session()`): thousands of open/close cycles cost ~1.5 min per discovery
run. Other processes wait until the command ends.

---

## 6. Scoring

`CompanyScorer.score(facts, profile) -> FitAssessment` is a pure function.

1. **Gates.** Core business in an excluded sector → overall 0, hidden.
2. **Impact fit.** Best tier among primary and secondary themes, plus theme
   bonuses, plus sector/client penalties, clamped to 0–5. Theme flags and
   client-sector flags are raised.
3. **Practical fit.** Base score + org-type bonus + size bonus; penalty when no
   office is within commute and there is no remote option; unknown funding
   raises `funding_unclear` (never excludes).
4. **Career fit.** Data-centrality score + business-model and org-type
   bonuses + bonus when hiring in a target role family now.
5. **Overall** = weighted mean of the three (weights in criteria, summing to 1).
   The three sub-scores are always stored and shown next to it; overall is
   used for ranking only.
6. **Flags and routing.** Flags never change a score. Low confidence or
   unknown key fields route the company to review instead of excluding it.
7. **Explanation.** Every contribution is recorded as `(component, delta, reason)`.

`RoleScorer` for postings: hard filters (excluded contracts, location/commute),
role-family match from title keywords, office-days curve (lowers, never
discards), seniority vs CV later. An excluded contract in a cold-signal family
at a company above `min_impact_fit` becomes a cold-application signal instead.

Calibration companies in the criteria become unit tests.

---

## 7. Job sources

**Company career boards (ATS feeds)**

| ATS | Endpoint | Format | Reader |
|---|---|---|---|
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true` | JSON | ✅ |
| Recruitee | `https://{slug}.recruitee.com/api/offers/` | JSON | ✅ |
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs` (US region only; EU-hosted boards have no public feed → check by hand) | JSON | ✅ |
| Workable | `https://apply.workable.com/api/v1/widget/accounts/{slug}` | JSON | ✅ |
| Teamtailor | `https://{careers site host}/jobs.rss` (custom domains included) | RSS | ✅ |
| Personio | `https://{slug}.jobs.personio.de/xml` | XML | ✅ |
| BambooHR | `https://{slug}.bamboohr.com/careers/list` | JSON | ✅ |
| Lever | `https://api.lever.co/v0/postings/{slug}?mode=json` (EU: `api.eu.lever.co`) | JSON | — |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{id}/postings` | JSON (verify) | — |
| Rippling | no public feed | — | check by hand |
| Fallback | schema.org `JobPosting` JSON-LD on the careers page | HTML |

**Findings from the first real run (Oct 2026)**: most careers links are plain pages, not ATS
feeds, so a careers-page change watcher (visible-text snapshots, added lines, target-role
highlighting) covers most companies. Welcome to the Jungle and some sites answer 403 to automated
clients: they are listed under "check manually" — we never disguise the client to get around
this. Pages that render jobs only with JavaScript are listed the same way. robots.txt handling
follows RFC 9309 (4xx = no restrictions; unreachable = disallow). Most careers pages are a shell
that loads jobs from a hosted board: `boards/detect.py` finds the board's address in the page's
HTML (only boards with a reader, only an unambiguous match; social links ignored) and the
company's board switches to that feed, keeping the user's careers link for reports. A board
missing from the public feed (404) or refused by robots.txt goes to "check by hand", not to run
problems. A site whose robots.txt can't be fetched at all is not read (RFC 9309) and is reported
as unreachable.

**Careers pages not linked from the homepage**: when discovery reads a website and no careers
page is known (no link, no board, nothing found), the company is flagged in `careers_missing`
(scores CSV column `careers_page`: linked / found by trying / missing / unknown).
`radar companies find-careers` (only when the user runs it, never in a scheduled run) tries,
for flagged companies only, the usual addresses (`/careers`, `/jobs`,
`/carrieres`, `/join-us`, `/recrutement`...) for companies with no careers page known. A page
counts only if it isn't the homepage again, and has careers wording in its title or headings or
an embedded readable board. Results go to `careers_found`; watched companies' boards are
upgraded at once, others use it when followed.

**Market-wide (role-led discovery)**: France Travail offers API (official,
free registration), climate job boards (check terms and feeds first).

**List source types** (`config/sources.yaml`, `type:`): `page` — any page linking to companies
directly or via one detail page each (portfolio cards, exhibitor lists; headline-like links are
ignored); `norrsken_impact100` — Norrsken's Impact/100 JSON (427 companies, 2022–2026, with
sectors, country and website); `sitemap_profiles` — a sitemap of one-profile-per-company pages
(e.g. Serena) with `include_categories` filtering on the fund's own labels. Lists of people (e.g.
The Independent's Climate 100) go through `radar add --text`, which extracts the organisations.

**Company directories (company-led discovery)**: VC/impact-fund portfolios,
startup campuses and programmes, B Corp / entreprise à mission / ESS
directories, public operators and agencies, nonprofits.

Welcome to the Jungle has no public API; many companies sync to it from their
ATS, so ATS feeds cover much of it.

---

## 8. Interfaces

- **CLI** — one command per pipeline (`radar add`, `discover`, `roles`, `companies …`, `demo`, `doctor`).
- **Digest** — weekly Markdown file: new matching jobs, new-to-you companies,
  open-application candidates, pipeline errors. Broad by default (the user
  reviews ~1h/week).
- **Review app** (later) — Streamlit: review queue, company explorer, new jobs,
  pipeline health.

---

## 9. Open decisions

- **LLM route** — decided: Claude Code via the user's subscription (`ClaudeCodeExtractor`, small model, per-run limit, runs in an empty temp dir; facts cached by content hash + extraction version). An API-key adapter can be added behind `LLMExtractor` for the cloud V2.
- **job_id for sources without an ATS** (France Travail).
- **Cloud V2**: scheduling and persistence (GitHub Actions + Parquet snapshots,
  or MotherDuck) behind the Repository interface.

---

## 10. Roadmap

| # | Step | Done when |
|---|---|---|
| 1 ✅ | Scaffold, domain ids/models, pack + criteria, profile loader, `radar profile check` | config validates with located errors; tests, ruff, mypy green |
| 2 ✅ | **Watch slice**: CSV of known companies (+ pending list) → polite HttpClient → Ashby, Recruitee, JSON-LD, careers-page change watcher → DuckDB → code-only prefilter → Markdown digest; `radar run-weekly` (later folded into the roles report, §12) | a weekly digest from the user's companies; idempotent re-runs |
| 3 🟡 | **Company-led discovery + scoring**: list pages (fund portfolios, event exhibitors) in `config/sources.yaml` → website text → facts via Claude Code (`claude -p --json-schema`, subscription) → CompanyScorer → follow / review / skip (auto-follow thresholds; `config/decisions.csv` overrides) → `radar discover` report; followed companies join the weekly watch | built and tested with fakes; real run pending Claude Code install |
| 3b | Role-led discovery: job boards from the user's list (Cleantech Open France, makesense, VC portfolio boards), France Travail API | the digest shows roles at companies the user didn't list |
| 3c ✅ | Role lists (to watch / to review / rejected) with freshness: due-only checks, new / still open / closed | `radar roles` twice in a week re-uses stored roles |
| 3d ✅ | Intake: `radar add` (names, URLs, CSV exports such as LinkedIn's, pasted text); name → website via Claude Code + web search, verified in code; free guesses as suggestions; scored by default, `--watch` for your own list; Claude usage measured per call (`llm_calls`) | names become verified, scored companies or pending with suggestions |
| 3e | Find similar companies: follow investors on top companies' sites, fund job boards (e.g. Marble), Claude lookalike search (capped, monthly + manual) | new companies scored through the same pipeline |
| 4 | Company-led discovery: directories, portfolios, public sector & nonprofits; alias table | sources return normalized, de-duplicated companies |
| 5 | Scoring: CompanyScorer + RoleScorer with calibration tests | table-driven tests incl. gates, tiers, penalties, contracts, calibration |
| 6 | Enrichment (LLM route decided then) | fake-extractor tests; one real run on 5 companies |
| 7 | Potential: monthly Claude "discovery researcher" (web search → candidate CSV) | candidates ingested as a source |
| 8 | Review app; Lever / SmartRecruiters readers (Greenhouse, Workable, Teamtailor, Personio, BambooHR + board detection ✅) | — |
| 9 | CV-based role matching | matches carry role_fit and reasons; CV local only |
| V2 | Cloud scheduling and persistence | runs without the Mac |

## 11. Target architecture (decided 2026-10-26)

**Aims.** (A) find roles and companies the user wouldn't find alone; (B) a credible,
public data-engineering project. **A wins when they conflict**: every addition for B must
also improve A. One codebase, two instances: *personal* (private criteria, lists,
decisions, database) and *demo* (climate pack, example criteria, public sources) for the
public repository.

**Runtime.** Mac now, cloud-ready: local Dagster + DuckDB; storage behind interfaces so
MotherDuck / Postgres and object storage can replace them later.

**Three kinds of data.**
1. *Raw* (immutable): every board / website / list response as received, partitioned by
   source and date, with fetch metadata. Can always be re-processed.
2. *Derived* (rebuildable from 1 + 3): parsed postings, website text, facts, scores,
   role lists, analytics.
3. *User input* (source of truth, backed up): companies, follow / reject decisions,
   applied statuses, notes.

**Layers.**
- Ingestion: polite HTTP client + board readers → raw zone.
- Processing (Python, pure domain): parse / normalise, website text, facts (Claude or
  keywords, versioned), scoring and decisions (plain code, weights from criteria).
- Modelling (dbt on DuckDB): staging per source → core (`dim_company` + aliases,
  `fct_posting_snapshots`, facts, scores) → marts (review queue, roles to watch,
  analytics). Scoring stays in Python; dbt works downstream of its output.
- Orchestration (Dagster): assets per step; schedules from the freshness table; manual-only
  assets (find-careers, Claude batches); asset checks (board suddenly empty, parse
  failures, stale facts); notifications after runs.
- Serving: FastAPI (review screens and decisions, add-a-company, start manual runs,
  read API), Markdown / email digest, dashboard on the marts.
- Operations: CI (tests, ruff, mypy, dbt build, Dagster definitions), Docker.

**Order.** 1) raw zone + re-parse from raw; 2) dbt staging / core / marts + tests +
posting snapshots; 3) Dagster assets, schedules, checks, dagster-dbt; 4) FastAPI review +
intake; 5) dashboard; 6) CI / Docker / demo instance; cloud later.

## 12. Simplification (2026-10-27)

- **One weekly output.** The first weekly digest (`radar run-weekly`: only new jobs, careers
  pages that changed, pending list) overlapped the roles report, which became the single
  weekly output (used by Dagster, the dashboard, the API and the demo); the pending list moved
  into it. A focused "what's new" report / email remains a planned feature; the old digest is
  in git history (last commit containing it: `9e4af36`).
- **One place for default paths** (`settings.DEFAULTS`), one shared date parser for the board
  readers, the model-route choice for `radar add` in `enrich/select.py`, dead code removed.
- **Comments**: every public function and class has a docstring (what + why);
  `docs/CODE_TOUR.md` maps the code.
