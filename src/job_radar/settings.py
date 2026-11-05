"""Where Mission-Aligned Careers reads and writes, overridable with JOB_RADAR_* environment
variables.

The defaults are the personal layout: config/ for what you edit (CV, criteria, company list),
data/ for what the tool writes. `radar setup` creates config/ from config_templates/.
The demo points them at demo_config/ and demo-run/, and sets JOB_RADAR_REPLAY_DIR so pages
and model answers are replayed offline.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from docx import Document
from pypdf import PdfReader

PREFIX = "JOB_RADAR_"


@dataclass(frozen=True)
class RadarPaths:
    """Every file and folder Mission-Aligned Careers reads or writes. The defaults are your
    personal layout; Docker and tests point them elsewhere.
    """

    criteria: Path = Path("config/criteria.yaml")
    packs: Path = Path("packs")
    companies: Path = Path("config/companies.csv")
    pending: Path = Path("config/companies_pending.csv")
    sources: Path = Path("config/sources.yaml")
    decisions: Path = Path("config/decisions.csv")
    roles_marks: Path = Path("config/roles.csv")  # roles you saved or ignored
    careers_checks: Path = Path("config/careers_checks.csv")  # careers pages checked by hand
    db: Path = Path("data/radar.duckdb")
    reports: Path = Path("data/digests")
    scores_csv: Path = Path("data/scores.csv")
    cache: Path = Path("data/cache")
    log_file: Path = Path("data/logs/radar.log")  # appended by every command, timestamped
    cv: Path = Path("config/cv")  # config/cv.pdf, .docx, .md or .txt (see find_cv); personal
    replay_dir: Path | None = None  # set: replay recorded pages and model answers (demo)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "RadarPaths":
        """The locations, with any JOB_RADAR_<NAME> environment variable overriding the default
        (an empty JOB_RADAR_REPLAY_DIR means live).
        """
        values: dict[str, Path | None] = {}
        for f in fields(cls):
            raw = env.get(f"{PREFIX}{f.name.upper()}")
            if raw is not None:
                values[f.name] = Path(raw) if raw.strip() else None
        return cls(**values)  # type: ignore[arg-type]


DEFAULTS = RadarPaths()  # the one place the default locations are written


def use_llm_from_env(env: Mapping[str, str]) -> bool:
    """Whether Dagster runs may use Claude Code: only when JOB_RADAR_USE_LLM is 1 / true / yes
    (the same opt-in as --use-llm on the command line)."""
    return env.get(f"{PREFIX}USE_LLM", "").strip().lower() in {"1", "true", "yes"}


CV_FORMATS = (".pdf", ".docx", ".md", ".txt")  # in this order when several are there


def find_cv(path: Path) -> Path | None:
    """The CV file: `path` itself if it is a file (e.g. demo_config/cv.md), else `path` with the
    first CV_FORMATS extension found (config/cv -> config/cv.pdf, config/cv.docx…); None if
    there is none."""
    if path.is_file():
        return path
    candidates = (path.with_suffix(ext) for ext in CV_FORMATS)
    return next((c for c in candidates if c.is_file()), None)


def cv_candidates(folder: Path) -> list[Path]:
    """Files in `folder` that could be a CV (PDF, Word, Markdown, text; not README.md), by name:
    `radar setup` lets you pick your master CV among them."""
    if not folder.is_dir():
        return []
    return sorted(
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in CV_FORMATS and p.name.lower() != "readme.md"
    )


def read_cv(path: Path) -> str | None:
    """The CV's text (PDF, Word, Markdown or text), read at each run so a new or edited CV
    re-matches every role; None if there is no CV or no text could be read from it (a scanned
    PDF, a broken file). It stays on this machine: never sent to a model."""
    found = find_cv(path)
    if found is None:
        return None
    try:
        text = _cv_text(found)
    except Exception:  # a broken CV file must never stop a run: it counts as no CV
        return None
    return text.strip() or None


def _cv_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    if suffix == ".docx":
        document = Document(str(path))
        cells = (c.text for t in document.tables for row in t.rows for c in row.cells)
        return "\n".join([*(p.text for p in document.paragraphs), *cells])
    return path.read_text(encoding="utf-8")
