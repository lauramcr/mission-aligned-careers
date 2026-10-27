# Language models in Mission-Aligned Careers

**Mission-Aligned Careers does not need a language model to run.** The whole pipeline — reading lists and
websites, scoring, watching job boards, reports — works without one. A model makes three
steps better. Each step sits behind a small interface, so any model can be plugged in.

| Step | Interface (code) | Without a model | With a model |
|---|---|---|---|
| Pasted text → organisations | `NameExtractor` — `enrich/resolve.py` | one name or URL per line | prose, any language; company vs fund / accelerator / event |
| Name → website | `WebsiteResolver` — `enrich/resolve.py` | free guesses (name.com, .fr…) as suggestions | needs **web search**; the website is always checked in plain code afterwards |
| Website text → fact sheet | `LLMExtractor` — `enrich/extractor.py` | keyword facts (rough, low confidence, never auto-followed) | structured facts limited to the pack's vocabulary |

The model **only extracts facts**. Scores are computed by plain, tested Python from your
criteria; the model never sees your preferences, notes, referrals, decisions or CV.
CV fit (ranking roles against your CV) is plain code too: your CV stays on your machine.

## What is available today

| Route | Status | Notes |
|---|---|---|
| **Claude Code** (`claude -p`, your Claude subscription) | ✅ implemented | all three steps; see [USING_WITH_CLAUDE.md](USING_WITH_CLAUDE.md) |
| **Keywords** (no model) | ✅ implemented | facts only; the fallback when no model is available |
| **Replay** (recorded answers) | ✅ implemented | used by the offline demo and tests |
| Local models (e.g. Ollama), other providers' APIs | not yet — follow the recipe below | |

`uv run radar doctor` shows which route is active.

## Plugging in another model

Write one class per step you want the model to handle (often just the first and the third),
then select it. The prompt, the answer schema and the validation already exist and are
model-neutral — your class only makes the call.

### 1. Facts: implement `LLMExtractor`

```python
from job_radar.domain.facts import CompanyFacts
from job_radar.enrich.extractor import (
    PROMPT_VERSION,
    ExtractionError,
    facts_json_schema,
    render_prompt,
    sanitize_facts,
)


class MyModelExtractor:
    metered = True  # counts against the per-run limit (--limit)

    def __init__(self, taxonomy, client):
        self._taxonomy, self._client = taxonomy, client

    @property
    def version(self) -> str:  # stored with every fact sheet: change it when the model
        return f"{PROMPT_VERSION}+mymodel-1"  # or prompt changes, so facts are re-extracted

    def extract(self, *, name: str, website: str, text: str) -> CompanyFacts:
        prompt = render_prompt(self._taxonomy, name=name, website=website)
        schema = facts_json_schema(self._taxonomy)  # JSON schema for the answer
        try:
            raw = self._client.complete_json(prompt=prompt, input=text, schema=schema)
        except SomeClientError as exc:
            raise ExtractionError(str(exc)) from exc  # the run continues; the company waits
        return sanitize_facts(raw, self._taxonomy)  # drops ids the pack doesn't define
```

Rules the pipeline relies on:
- Return a `CompanyFacts`; raise `ExtractionError` on any failure (never return a guess).
- Ask for **structured output** (JSON schema / JSON mode) when the model supports it; always
  pass the result through `sanitize_facts`.
- Send only `text` (website text + list descriptions) and the pack vocabulary — never the
  user's criteria or notes.

### 2. Pasted text: implement `NameExtractor`

`extract(text) -> list[Candidate]`, where each `Candidate` has `name`, `website` (or `None`),
`context` and `kind` (`company`, `fund`, `accelerator`, `event`, `other`). The instruction
and JSON schema used for Claude are in `enrich/resolve.py` (`ClaudeCodeNameExtractor`,
`_NAMES_SCHEMA`) and can be reused as-is. Small local models do this step well.

### 3. Websites: implement `WebsiteResolver` (only with web search)

`resolve(name, context) -> Resolution(website, source_url, confidence)`. A model **without
web search will invent plausible addresses** for small start-ups — don't wire one in here;
leave the resolver out and names go to the pending list with free suggestions instead.
Whatever the resolver returns is verified in plain code (`verify_website`: the site loads,
the name appears on it, it isn't a parked domain).

### 4. Select it

- Facts: `enrich/select.py` → `select_extractor()` picks the extractor for `radar discover`,
  Dagster and the API. Add your route there (e.g. from a setting or an environment variable).
- Names and websites: `radar add` builds them in `cli.py` (`add_command`).
- Credentials (API keys) come **only from environment variables**, never from files in the
  repository.

### 5. Test it

- Unit-test your class with a fake client (no network, no real model) — see
  `tests/unit/enrich/test_extractor.py` for the Claude route's tests.
- Record real answers once with the recording wrappers in `enrich/replay.py`
  (`RecordingExtractor`, `RecordingNameExtractor`) and replay them in tests and the demo.

## Choosing a model

- **Quality:** structured extraction from short web pages is an easy task for current
  models; small local models (7–8B) handle pasted lists well and facts acceptably.
- **Cost and limits:** facts are cached per website version and capped per run (`--limit`),
  so a weekly run makes few calls after the first.
- **Privacy:** only public website text, list descriptions and your pasted notes are sent. A
  local model keeps even those on your machine.
