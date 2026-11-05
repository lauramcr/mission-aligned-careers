# Set it up for your own job search

About 15 minutes the first time. Everything personal goes in **one folder, `config/`**: your
CV, your preferences, your lists and your company list. `config/` is in `.gitignore`, so it is
never committed or shared. What the tool writes (database, reports, scores) goes in `data/`,
also private.

```
config/
├── cv.pdf                 ← your master CV (or cv.docx, cv.md, cv.txt)
├── criteria.yaml          ← what you care about, where, which roles
├── sources.yaml           ← lists of companies to read (fund portfolios, rankings…)
├── companies.csv          ← companies you already know (always watched)
├── decisions.csv          ← your follow / worth a look / reject choices
├── roles.csv              ← roles you saved or ignored (from the dashboard)
├── careers_checks.csv     ← careers pages you checked by hand or ignored
├── old_cvs/               ← CVs put aside by setup (never deleted)
└── README.md              ← what each file is
```

The starting files come from `config_templates/`. The fictional demo person lives in
`demo_config/`, so the demo never touches your folder.

## 1. Install (once)

You need [uv](https://docs.astral.sh/uv/) (it installs the right Python for you) and git.

```bash
git clone https://github.com/lauramcr/mission-aligned-careers.git
```

```bash
cd mission-aligned-careers && uv sync
```

Optional: try the demo first (fictional data, the dashboard opens): `uv run radar demo`.

## 2. See where you are

```bash
uv run radar
```

It prints a checklist (✓ in place · ! optional or worth a look · ✗ missing) and the next step.

## 3. Fill in your setup

```bash
uv run radar setup
```

A page opens in your browser (`http://localhost:8000/setup`, also *Update setup* on the
dashboard): your CV, what you're looking for, lists to read, companies you know and those
waiting for a website, each explained with sample answers and saved on its own; *Save all and
run* then runs what is due and shows the progress. Each step is described below.

It shows what you already have, so you can come back at any time to change an answer.
Nothing is deleted.

1. **Your CV.** Drag your master CV into the terminal (that types its path) and press Enter.
   PDF, Word, Markdown or text all work. It is copied to `config/` as `cv.pdf` (or `.docx`…),
   and any other CV there is moved to `config/old_cvs/`.
   - It ranks roles by **skills fit**: *"CV fit 4.3 · matches SQL, dbt, Python · missing
     Stakeholder management"*. It never moves a role from one list to another.
   - It is read again at every run: replace it and every role is re-matched.
   - It stays on your computer. No model ever sees it.
   - A scanned PDF (an image of text) can't be read: export it as Word, or as a PDF with
     selectable text. `uv run radar` tells you whether it was read.
2. **What you're looking for.** Short questions with numbered choices from the climate pack,
   so there are no typos:
   - where you live and your longest commute;
   - where fully remote roles may be based;
   - the climate themes that matter most, and the ones that are also OK;
   - sectors you'd never work for;
   - the roles you want;
   - contracts you won't take;
   - the organisations you'd join;
   - what matters most: mission first, balanced or career first.
3. **Lists to follow.** Keep the suggested public lists (fund portfolios, rankings) and paste
   other addresses. They are re-read every 30 days.
4. **Companies you're interested in following.** Paste names or websites, one per line, or the path to a
   CSV such as your LinkedIn export (Settings → Data privacy → *Get a copy of your data*, then
   *Company Follows.csv*). A name without a website goes to the *waiting for your input* list
   until you add it.

### Your own themes, sectors and roles

The climate pack's vocabulary is a starting point, not a limit. On the setup page, type your own
in *Fields of interest*, *Sectors you would never work for* or *Kinds of roles* (or tick some
under *Need inspiration?*). Each term you add gets words to look for, editable on the page:

- a theme or sector: words on a company's website (*Biotech*: biotech, biotechnology, synthetic
  biology);
- a kind of role: words in job titles (*Biostatistics*: biostatistician, biostatistics). Job
  titles are matched from the start of a word, so give the forms titles use.

They are kept in `config/my_vocabulary.yaml` and merged with the pack's when the tool runs.
Adding or changing one re-classifies companies from the website text already stored, at the next
run (free on the keyword route). With `--use-llm`, Claude Code's list of themes and sectors
includes your terms (not whether you want them).

### Always follow

Each company you add has an **Always follow** tick on the setup page (column `always_follow` in
`config/companies.csv`). Ticked, the default: it is followed (its job board watched every week)
whatever its score, since you chose it. Unticked: its score decides, followed or *worth a look*,
but it is never rejected. Untick it for a long import (e.g. LinkedIn) where some companies are
far from what you want. The setup page has a tour of its own (*Take the tour*, or
`uv run radar setup --tour`).

### Companies from a CSV

The file needs a header row and a name column: `name`, `company`, `company name`,
`organization`, `organization name`, `organisation`, `entreprise` or `société` (any case). A
website column is optional: `website`, `url`, `company url`, `site`, `site web` or `web`; a job
board link there (LinkedIn, Welcome to the Jungle…) is kept as a note instead. Other columns are
kept as notes. Comma-separated, UTF-8 (Excel: "CSV UTF-8"); empty rows and duplicates are
skipped. LinkedIn's *Company Follows* export works as is.

## 4. Run it

```bash
uv run radar start
```

It reads your lists and the companies' websites, scores every company, reads the job boards of
the companies you follow, writes the reports (`data/digests/`) and the scores CSV
(`data/scores.csv`), then opens your dashboard at http://localhost:8000. Ctrl+C stops it.
While it runs, the screen shows each step and each website or job board being read; the full
record, timestamped, is appended to `data/logs/radar.log` (`uv run radar --verbose start`
shows every detail on screen).
Run it again whenever you like: lists and websites read in the last 30 days and job boards
checked in the last 7 days are not fetched again (`--force` re-reads everything).

On the dashboard and in the roles report:

- **To watch / to review / rejected**: open roles, best CV fit first, each with its reason.
- **Check careers pages by hand**: good companies whose jobs the tool can't read for you,
  best score first, with the link and the reason. For example: no careers page found, jobs
  only on LinkedIn, a site that blocks tools or asks them not to read it (the tool respects
  that), or a page that needs JavaScript. The scores CSV has the same information in its
  `jobs_check` column.
- **Waiting for your input**: companies without a website. Add it in
  `config/companies_pending.csv`, then run `uv run radar companies import`.

### With or without a language model

By default **no model is used** and every run says so on its first line. Company facts come
from free keyword matching. That's rough, so nothing is followed automatically: good matches
land in *worth a look* for you to decide. Pasted lists are read line by line.

Add `--use-llm` to use Claude Code with your Claude subscription (no API key), e.g.
`uv run radar start --use-llm`. It sends company names, websites and website text, never your
CV or criteria. See [LLM.md](LLM.md).

## 5. Fine-tune your criteria (optional)

Setup asks the main questions; `config/criteria.yaml` has more settings, each explained in the
file. Open it in any text editor. The words you can use (theme ids, sectors, organisation
types, role families) are defined in `packs/climate/taxonomy.yaml`, with a description of each.
After each edit, check it:

```bash
uv run radar profile check
```

It lists every problem at once, for example a misspelt theme or weights that don't add up to 1.

Each company gets **three scores from 0 to 5**, and an **overall** score that mixes them:

| Score | Question it answers | Set in |
|---|---|---|
| impact | Does what they do matter to you? | `impact` |
| practical | Is it the kind of organisation you'd join? | `practical` |
| career | Will your skills be central there? | `career` |

Section by section:

- **`location`** — where you live and how far you'll go.
  - `home`, `max_commute_minutes`: your home and the longest commute you accept.
  - `also_accept_places`: places that count as within reach (setup asks them). A role is
    *within reach* when its location names your home town or one of these: commute minutes
    aren't computed yet, so add the towns you'd commute to.
  - `accept_remote_in`: where a fully remote role may be based (e.g. `France`, `Europe`).
  - A role elsewhere is not thrown away: it goes to *to review* with the reason.
- **`impact`** — the climate themes you care about, in tiers.
  - `tiers`: tier A themes score 5, tier B 3.5, tier C 2. Move themes between tiers; delete
    the ones you don't care about.
  - `theme_bonus`: a small extra for your favourite themes.
  - `theme_flags`: themes that always raise a flag for you to look at (e.g. carbon offsets).
  - `exclude_core_sectors`: sectors you'll never work for (e.g. `oil_and_gas`); those
    companies are hidden.
  - `penalties`: sectors that lower the impact score without excluding (e.g. `aviation: -1`).
  - `client_sector_flags`: flag a company whose big clients are in a sector (no penalty).
- **`practical`** — the kinds of organisations you'd join.
  - `accepted_org_types`: startup, scaleup, public agency, nonprofit…
  - `base_score`, `org_type_bonus`, `size_bonus`: e.g. a bonus for startups or for teams
    under 100 people.
- **`career`** — how central data, or your field, is to the company.
  - `data_centrality_scores`: core (data is the product), supporting, minimal.
  - Bonuses for business models, organisation types, or an open role in your field right now.
- **`roles`** — the jobs you want.
  - `target_families`: your role families, from the pack's 20 (data engineering, data
    analysis / BI, project management, product management, sustainability / climate strategy,
    energy markets, GIS / remote sensing, research, customer success, design, sales,
    marketing, people / recruiting, finance, software engineering, operations…). A role's
    family comes from title keywords (English and French), listed in
    `packs/climate/taxonomy.yaml`.
  - `excluded_families`: families you never want (e.g. `[sales_business_development]`): their
    roles are rejected. Your target families always win (a "Sales Data Analyst" stays data
    analysis). `uv run radar setup` → *4 only role families* adds or excludes families, showing
    how many open roles each has at your companies.
  - `contracts`: accepted and excluded (e.g. no internships).
  - `cold_application_signal`: an excluded contract (an internship) at a high-impact company
    in your field is shown as a hint to send an open application.
  - `office_days`: how many office days a week you prefer. This only changes the role score,
    never discards a job.
  - `languages`: the languages you work in.
  - `show_per_company`: at most this many roles per company in each list, best CV fit first
    (default 5; 0 shows all). The dashboard's "+ N more" row shows the rest.
- **`overall.weights`** — how much each score counts. They must add up to 1. The default
  `impact 0.5, career 0.3, practical 0.2` means mission first.
- **`thresholds`** — the cut-offs that turn scores into decisions:

  | Setting | What it decides |
  |---|---|
  | `show_company_for_review.impact_fit_min` | the lowest impact still shown as *worth a look* |
  | `auto_follow` | when a company is followed automatically (its job boards watched every week) |
  | `check_by_hand.recheck_days` | how long a careers page you marked *Checked* stays hidden (default 30) |
  | `check_by_hand.min_overall` | which *worth a look* companies join the **check careers pages by hand** list when their careers page can't be read (default 4.0) |
  | `other_roles` | when a title outside your role families goes to *to review* instead of *other roles at your companies*: CV fit at least `cv_fit_min` (default 3.5) on at least `min_skills` skills the pack doesn't mark generic (default 3) |
  | `open_application_candidate`, `notify_job` | not used yet (planned: open-application suggestions, role alerts); checked for typos only |

- **`calibration`** — companies you know you love, or feel cold about, each with a sentence on
  why. Not used by the tool yet; planned as checks that the scores match your judgement.

Changing your criteria re-scores everything for free: facts about companies are stored, so no
model is called again.

## 6. Every week

Run `uv run radar start` when you want fresh results, or let Dagster do it:
`./scripts/dagster-dev.sh`, then switch on `weekly_radar_schedule` under *Automation*. The
schedule only fires while that script is running (set `JOB_RADAR_USE_LLM=1` first to use
Claude Code).

To move a company yourself, use the menu on its row in the dashboard (*Follow*, *Worth a
look*, *Reject*, *Automatic*) or `uv run radar decide <name or website> follow|review|reject|auto`.
It applies at once and is kept in `config/decisions.csv` (`company,decision,note`), which you
can also edit by hand. Your decisions override the scores and your own list: a company you
reject or put in worth a look is no longer watched.

## Troubleshooting

- **`uv: command not found`**: uv isn't on your PATH in this terminal. Open a new terminal
  or run `source ~/.zshrc`. On a Mac with uv installed by pip, add
  `export PATH="$HOME/Library/Python/3.13/bin:$PATH"` to `~/.zshrc`.
- **`unrecognized subcommand 'radar'`** or **`Failed to spawn: radar`**: run commands as
  `uv run radar …` from the project folder. After pulling a new version, run
  `uv sync --reinstall-package mission-aligned-careers` once.
- **`config/criteria.yaml not found`**: run `uv run radar setup`.
- **No CV fit on roles**: `uv run radar` says whether a CV was found and read.
- **`--use-llm` stops with "Claude Code not available"**: install and sign in
  ([LLM.md](LLM.md)), or run without `--use-llm`.
- **A company you expected isn't followed**: check its scores and reason with
  `uv run radar companies scores`, or follow it from the dashboard or with
  `uv run radar decide <name> follow`.
