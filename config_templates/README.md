# Your config folder

Everything here is personal and never committed (`config/` is in `.gitignore`).
`uv run radar setup` creates and fills it; run it again any time to change your answers.
`uv run radar` shows what is in place.

- `cv.pdf`, `cv.docx`, `cv.md` or `cv.txt`: **your master CV.** It ranks roles by skills fit,
  is read again at every run, stays on your computer and is never sent to a model. Older CVs
  are kept in `old_cvs/`.
- `criteria.yaml`: your preferences (location, climate themes, roles, contracts, weights).
  Setup asks the main ones; the file explains the rest. Check it with
  `uv run radar profile check`.
- `companies.csv`: companies you're interested in following. One per line: name, website,
  careers page (optional), notes, referral (someone you know there), always_follow (empty or
  `yes`: followed whatever its score; `no`: the score decides, never rejected).
- `sources.yaml`: lists of companies to read (fund portfolios, rankings, exhibitor lists,
  a Y Combinator directory page).
- `my_vocabulary.yaml` (appears when you add your own terms on the setup page): themes, sectors
  and kinds of roles you added, with the words to look for.
- `decisions.csv` (appears when you use it): your follow / worth a look / reject choices on companies (the dashboard menu
  or `uv run radar decide` writes it).
- `roles.csv` (appears when you use it): roles you saved (★ on the dashboard; a saved role keeps
  its own list) or ignored.
- `careers_checks.csv` (appears when you use it): careers pages you checked by hand (hidden for
  `recheck_days`, 30 by default) or ignored (hidden for good).
- `companies_pending.csv` (appears later): companies without a website yet; add it, then run
  `uv run radar companies import`.

Guide: docs/SETUP.md.
