"""Where Mission-Aligned Careers reads and writes, overridable with JOB_RADAR_* environment
variables.

The defaults are the personal layout (config/, data/). The Docker demo points them at demo/
and demo-run/, and sets JOB_RADAR_REPLAY_DIR so pages and model answers are replayed offline.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

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
    db: Path = Path("data/radar.duckdb")
    reports: Path = Path("data/digests")
    scores_csv: Path = Path("data/scores.csv")
    cache: Path = Path("data/cache")
    cv: Path = Path("config/cv.md")  # plain text / Markdown; personal, gitignored
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


def read_cv(path: Path) -> str | None:
    """The CV's text, read at each run (a new or edited CV re-matches every role); None if
    there is no CV yet. It stays on this machine: never sent to a model."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None
