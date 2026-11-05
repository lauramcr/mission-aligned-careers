"""The settings every asset shares: file locations and the fact-extraction choice."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dagster import ConfigurableResource

from job_radar.enrich.extractor import DEFAULT_MODEL
from job_radar.enrich.replay import RecordingStore, ReplayExtractor
from job_radar.enrich.select import ExtractorChoice, select_extractor
from job_radar.enrich.usage import UsageRecorder
from job_radar.pipeline.discover import DEFAULT_EXTRACTION_LIMIT
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile, Taxonomy
from job_radar.settings import DEFAULTS, RadarPaths
from job_radar.sources.marks_csv import YourMarks
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import CACHE_KEEP_SECONDS, DiskCache, HttpClient, HttpxClient
from job_radar.web.replay import load_recordings


class RadarResource(ConfigurableResource):  # type: ignore[type-arg]
    """Paths are relative to the project folder (where `dagster dev` is started)."""

    criteria: str = str(DEFAULTS.criteria)
    packs: str = str(DEFAULTS.packs)
    companies: str = str(DEFAULTS.companies)
    pending: str = str(DEFAULTS.pending)
    db: str = str(DEFAULTS.db)
    sources: str = str(DEFAULTS.sources)
    decisions: str = str(DEFAULTS.decisions)
    roles_marks: str = str(DEFAULTS.roles_marks)
    careers_checks: str = str(DEFAULTS.careers_checks)
    reports: str = str(DEFAULTS.reports)
    scores_csv: str = str(DEFAULTS.scores_csv)
    cache: str = str(DEFAULTS.cache)
    cv: str = str(DEFAULTS.cv)
    replay_dir: str = (
        ""  # set (e.g. "demo_config"): replay recorded pages and model answers, offline
    )
    use_llm: bool = False  # Claude Code only when asked (JOB_RADAR_USE_LLM=1), else keywords
    claude_model: str = DEFAULT_MODEL
    extraction_limit: int = DEFAULT_EXTRACTION_LIMIT

    @classmethod
    def from_paths(cls, paths: RadarPaths, *, use_llm: bool = False) -> "RadarResource":
        """The resource for a set of locations (from JOB_RADAR_* variables)."""
        return cls(
            use_llm=use_llm,
            criteria=str(paths.criteria),
            packs=str(paths.packs),
            companies=str(paths.companies),
            pending=str(paths.pending),
            db=str(paths.db),
            sources=str(paths.sources),
            decisions=str(paths.decisions),
            roles_marks=str(paths.roles_marks),
            careers_checks=str(paths.careers_checks),
            reports=str(paths.reports),
            scores_csv=str(paths.scores_csv),
            cache=str(paths.cache),
            cv=str(paths.cv),
            replay_dir=str(paths.replay_dir) if paths.replay_dir else "",
        )

    def extractor(self, taxonomy: Taxonomy, recorder: UsageRecorder) -> ExtractorChoice:
        """Replayed model answers in replay mode; otherwise Claude Code or free keywords."""
        if self.replay_dir:
            store = RecordingStore(Path(self.replay_dir) / "recordings" / "llm")
            return ExtractorChoice(ReplayExtractor(store, taxonomy), missing=None)
        return select_extractor(
            taxonomy, use_llm=self.use_llm, model=self.claude_model, recorder=recorder
        )

    def profile(self) -> Profile:
        """Your validated criteria and pack."""
        return load_profile(Path(self.criteria), Path(self.packs))

    def marks(self) -> YourMarks:
        """Your saved / ignored roles and checked / ignored careers pages."""
        return YourMarks.load(Path(self.roles_marks), Path(self.careers_checks))

    def repository(self) -> DuckDBRepository:
        """The database at the configured location."""
        return DuckDBRepository(Path(self.db))

    @contextmanager
    def http(self) -> Iterator[HttpClient]:
        """The HTTP client for a run: recorded pages in replay mode, otherwise the polite client
        with its cache.
        """
        if self.replay_dir:  # recorded pages only: nothing is fetched
            yield load_recordings(Path(self.replay_dir) / "recordings" / "http")
            return
        cache = DiskCache(Path(self.cache))
        with HttpxClient(cache=cache) as client:
            yield client
        cache.prune(CACHE_KEEP_SECONDS)  # after the run: only pages it didn't need go
