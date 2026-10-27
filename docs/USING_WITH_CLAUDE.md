# Using Mission-Aligned Careers with Claude

Mission-Aligned Careers runs without any language model: the free route reads websites, extracts rough
"keyword facts" and parses pasted lists line by line. **Claude Code** makes three steps much
better, using your Claude subscription (Pro or Max) — no API key, no per-use billing:

| Step | Without Claude | With Claude Code |
|---|---|---|
| Pasted notes → company list | one name or URL per line only | free text, prose, French/English; tells companies from funds, accelerators and events |
| Name → website | free guesses as suggestions (you confirm) | web search, then the website is **checked in plain code** |
| Website → fact sheet | keyword facts (rough, never auto-followed) | structured facts limited to the pack's vocabulary |

Scoring is always plain Python with your criteria: Claude never sees your preferences, notes,
referrals or decisions, and never decides a score.

---

## 1. Install and sign in (once)

1. Install the `claude` command-line tool following Anthropic's official setup page
   (Claude Code → *Set up*). On a Mac this is a one-line installer in the Terminal.
2. Sign in: run `claude` once in a terminal and choose to log in with your **Claude account**
   (your subscription). Then type `/exit`.
3. Check from the project folder:
   ```bash
   uv run radar doctor
   ```
   You should see `✓ Claude Code … used for names, websites and facts`.

**Your credentials:** Claude Code keeps its login itself (on macOS, in the encrypted Keychain).
Mission-Aligned Careers never sees, stores or sends them: it only runs the `claude` command.

**Don't set `ANTHROPIC_API_KEY`** unless you want to pay per use: when it is set, Claude Code
uses it instead of your subscription. `radar doctor` warns you if it is set.

---

## 2. Everyday use

### Paste notes from an event, an article, a newsletter
```bash
uv run radar add --text "$(pbpaste)"          # what you just copied (macOS clipboard)
uv run radar add --text "Met Kanop and Wastetide at ChangeNOW; Raise runs an accelerator"
```
Claude turns the text into a list (name, website if given, context, kind). Companies are
looked up, checked and scored; **funds, accelerators and events** are listed under
"Lists you could add" — add their portfolio or cohort page to `config/sources.yaml` and every
company on it gets scored at the next `radar discover`. Anything unresolved goes to
`config/companies_pending.csv` with the reason.

Add `--watch` to put companies on your own list (always watched) instead of scoring them;
`--note` and `--referral` stay on your list and are never sent to Claude.

### Other ways in
```bash
uv run radar add "Kanop" https://www.wastetide.com      # names and/or websites
uv run radar add --file "Company Follows.csv"            # e.g. LinkedIn data export
```

### Score everything, see the best ones
```bash
uv run radar discover --limit 20       # lists + websites; Claude reads at most 20 companies
uv run radar companies scores --outcome review --top 30
uv run radar companies scores --csv data/scores.csv      # open in Excel / Numbers
```
`--limit` caps Claude calls per run (the rest wait for the next run); facts are re-read only
when a website changed or after 30 days, so later runs are cheap.

### Find open roles
```bash
uv run radar roles                     # to watch / to review / rejected → data/digests/
uv run radar companies find-careers    # only when you ask: try /careers, /jobs… where none is known
```

### Decide
Add lines to `config/decisions.csv` (`company,decision,note` with `follow` or `reject`), or use
the API (`./scripts/api-dev.sh`, then `POST /companies/{id}/decision` at
http://localhost:8000/docs). Decisions apply on the next run.

### Schedule it
`./scripts/dagster-dev.sh` → http://localhost:3000 → switch on `weekly_radar_schedule`
(Mondays 07:00). Claude is used by the scheduled run too, within `--limit`-style caps.

---

## 3. What is sent to Claude — exactly

| Call | Sent | Tools Claude may use | Answer shape |
|---|---|---|---|
| names | your pasted text | none | list of {name, website, context, kind} |
| website lookup | a company name + the context line it came with | WebSearch, WebFetch | {website, source, confidence} |
| facts | the company's website text + list descriptions; the pack's vocabulary | none | fact sheet limited to the pack's ids |

Every call runs `claude -p` non-interactively in an **empty temporary folder** (so no project
files or instructions are loaded), with `--permission-mode dontAsk`, a **JSON schema** that
forces the answer's shape, and `--model haiku` by default (`--model sonnet` for better
quality at a higher usage cost). Every call is logged in the `llm_calls` table.

**Never sent:** your criteria and weights, notes, referrals, decisions, CV, scores.

---

## 4. Usage limits

Claude Code shares your subscription's usage limits with claude.ai (a rolling few-hour window
and a weekly limit). To keep runs small:
- keep `--limit` modest (10–30) and let freshness do the rest;
- `haiku` is the cheapest model; facts are cached per website version;
- check what Mission-Aligned Careers used:
  ```bash
  uv run radar db query "SELECT purpose, count(*), sum(input_tokens), sum(output_tokens)
                         FROM llm_calls GROUP BY 1"
  ```
- and your overall usage with `/usage` inside `claude`.

If the limit is reached, calls fail cleanly: companies wait for the next run, nothing breaks.

---

## 5. Driving Mission-Aligned Careers from a Claude Code session

You can also let Claude Code operate the tool for you: open a terminal in the project folder,
run `claude`, and ask in plain words. It reads `CLAUDE.md` (how the project works) and runs the
`radar` commands itself. Examples:

> Here are my notes from VivaTech: *(paste)*. Add the companies, then tell me which ones are
> worth a look and why.

> Run the weekly roles check and summarise the roles to watch near Paris.

> Which companies scored above 4.5 but have no careers page? Try find-careers on them.

> Follow Overstory and Back Market, then check their open roles.

Good to know: in this mode Claude Code can read the project's files, including your personal
`config/` files and database, to answer you — that's your local data shared with your own
Claude session. The pipeline calls in section 3 never include them.

---

## 6. Without Claude

Everything still works with the free route; `radar doctor` tells you which one is active.
To see the full Claude-quality flow without an account, run the offline demo:
```bash
uv run radar demo --open
```
It replays recorded model answers (`demo/recordings/llm`). Once Claude Code is installed,
`uv run radar demo --record` re-records the names and facts with Claude.
