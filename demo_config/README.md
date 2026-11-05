# The demo kit (fictional)

Everything here is invented for `radar demo`: the person, the companies, their websites and
job postings. Domains use the reserved `.example` suffix, so none of them can exist.

`radar demo` opens this setup read-only in your browser (the setup page); *Run the demo* then
copies these files into `demo-run/config/` and runs the tool on the copy (data in
`demo-run/data/`), so the dashboard's buttons work without ever changing this folder; the API
refuses to write here.

| File | What it is |
|---|---|
| `cv.md` | Alex Martin, a fictional data analyst in Paris |
| `criteria.yaml` | Alex's preferences, derived from the CV by hand (target roles, themes, weights) |
| `paste.txt` | messy notes from an event, mostly English with some French |
| `companies.csv` | the one company already on Alex's own list |
| `companies_pending.csv` | two companies Alex noted without a website (Fermes Solaires du Sud, Atelier Cargo Vélo): *waiting for your input* |
| `my_vocabulary.yaml` | Alex's own term, *Deep tech*, added to the pack's themes |
| `sources.yaml` | a climate fund's portfolio page, read as a list |
| `recordings/http/` | the web: homepages, careers pages, a portfolio page and job-board feeds in the real formats of Ashby, Workable, Teamtailor and Personio (`index.yaml` maps URL → file) |
| `recordings/llm/` | model answers: names from the notes, website lookups, fact sheets |

Each company shows one behaviour:

| Company | Shows |
|---|---|
| Voltaïa | Ashby board; a role **to watch** and a rejected one |
| Boucle | Workable board; a role **to watch** |
| Canopée Data | careers page embedding a Teamtailor board, **detected**; a remote role |
| HeatLoop | Personio; a matching role in Berlin → **to review** |
| Terroir+ | on Alex's own list; plain careers page with an internship → open-application signal |
| Mareo | no careers link → flagged; `find-careers` finds `/careers` |
| Recharge Commune | Greenhouse board not on the public feed → **check by hand** |
| Hydro Nord | website blocks automated access; facts from one list line → **worth a look** |
| AdBoost | **rejected**: no climate theme |
| PetroSense | **rejected**: excluded sector (oil & gas) |
| Fonds Vert Avenir · Lab Climat · ChangeNOW | a fund, an accelerator, an event → **suggested lists** |

**Recordings.** The model answers in `recordings/llm/` are hand-written samples in the exact
format a live recording uses. With Claude Code installed, `uv run radar demo --record`
replaces the names and facts with real Claude answers (website lookups stay hand-written: a
web search can't find fictional companies). Add a URL to `recordings/http/index.yaml` to
extend the world; anything not recorded is never fetched.
