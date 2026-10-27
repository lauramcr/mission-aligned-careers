"""The pipelines as Dagster assets. Each asset calls an existing pipeline function and
reports its numbers as metadata (shown in the Dagster UI).

    user_companies ─► company_scores ─► scores_csv
                                    └─► open_roles
    careers_pages  (manual only: never in the weekly job)
"""

from datetime import UTC, datetime
from pathlib import Path

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from job_radar.enrich.usage import UsageRecorder
from job_radar.matching.roles import RoleOutcome
from job_radar.notify.discover_report import write_discover_report
from job_radar.notify.roles_report import write_roles_report
from job_radar.notify.scores_csv import export_scores
from job_radar.orchestration.resources import RadarResource
from job_radar.pipeline.discover import run_discover
from job_radar.pipeline.find_careers import run_find_careers
from job_radar.pipeline.roles import run_roles
from job_radar.pipeline.watch import run_import
from job_radar.scoring.decide import Outcome
from job_radar.settings import read_cv
from job_radar.sources.decisions_csv import load_decisions
from job_radar.sources.list_page import load_sources


@asset(group_name="companies", description="Your own company list (companies.csv) in the database.")
def user_companies(radar: RadarResource) -> MaterializeResult[None]:
    """Your own list (companies.csv) loaded into the database."""
    repo = radar.repository()
    with repo.session():
        summary = run_import(
            repo,
            companies_csv=Path(radar.companies),
            pending_csv=Path(radar.pending),
            now=datetime.now(UTC),
        )
    return MaterializeResult(
        metadata={
            "companies": summary.companies,
            "pending": summary.pending,
            "warnings": MetadataValue.md("\n".join(f"- {w}" for w in summary.warnings) or "none"),
        }
    )


@asset(
    group_name="companies",
    deps=[user_companies],
    description="Lists read when due, websites read, facts extracted, every company scored "
    "and decided (follow / review / rejected). Writes the discovery report.",
)
def company_scores(context: AssetExecutionContext, radar: RadarResource) -> MaterializeResult[None]:
    """Lists and websites read when due, facts extracted, every company scored and decided;
    writes the discovery report.
    """
    profile, repo = radar.profile(), radar.repository()
    recorder = UsageRecorder()
    choice = radar.extractor(profile.taxonomy, recorder)
    if choice.missing:
        context.log.info(f"Using free keyword facts: {choice.missing}")
    with radar.http() as http, repo.session():
        result = run_discover(
            repo,
            http,
            profile,
            choice.extractor,
            sources=load_sources(Path(radar.sources)),
            decisions=load_decisions(Path(radar.decisions)),
            pending_csv=Path(radar.pending),
            now=datetime.now(UTC),
            extraction_limit=radar.extraction_limit,
            extractor_missing=choice.missing,
            recorder=recorder,
        )
    report = write_discover_report(result, Path(radar.reports))
    outcomes = [i.outcome for i in result.items]
    return MaterializeResult(
        metadata={
            "companies": len(result.items),
            "follow": outcomes.count(Outcome.FOLLOW),
            "review": outcomes.count(Outcome.REVIEW),
            "rejected": outcomes.count(Outcome.REJECTED),
            "read_by_claude": result.extracted,
            "facts": (
                "replayed (demo)"
                if radar.replay_dir
                else "keywords (rough)"
                if choice.rough
                else "claude"
            ),
            "problems": len(result.errors),
            "report": MetadataValue.path(str(report)),
        }
    )


@asset(
    group_name="companies",
    deps=[company_scores],
    description="Every scored company, best first, as a CSV for Excel / Numbers.",
)
def scores_csv(radar: RadarResource) -> MaterializeResult[None]:
    """Every scored company, best first, as a CSV."""
    repo = radar.repository()
    with repo.session():
        path = export_scores(repo, Path(radar.scores_csv))
        rows = len(repo.assessments())
    return MaterializeResult(metadata={"rows": rows, "path": MetadataValue.path(str(path))})


@asset(
    group_name="roles",
    deps=[company_scores],
    description="Jobs at every watched company (boards checked when due, 7 days), sorted into "
    "to watch / to review / rejected. Writes the roles report.",
)
def open_roles(radar: RadarResource) -> MaterializeResult[None]:
    """Followed companies' job boards checked when due, roles sorted and ranked by CV fit;
    writes the roles report.
    """
    profile, repo = radar.profile(), radar.repository()
    with radar.http() as http, repo.session():
        report = run_roles(
            repo, http, profile, now=datetime.now(UTC), cv_text=read_cv(Path(radar.cv))
        )
    path = write_roles_report(report, profile, Path(radar.reports))
    watch = report.by_outcome(RoleOutcome.WATCH)
    return MaterializeResult(
        metadata={
            "checked": report.checked,
            "up_to_date": report.up_to_date,
            "open_roles": len(report.roles),
            "to_watch": len(watch),
            "new_to_watch": sum(report.is_new(r) for r in watch),
            "cv_matched": report.has_cv,
            "to_review": len(report.by_outcome(RoleOutcome.REVIEW)) + len(report.page_mentions),
            "couldnt_check": len(report.unreadable),
            "problems": len(report.errors),
            "report": MetadataValue.path(str(path)),
        }
    )


@asset(
    group_name="manual",
    deps=[company_scores],
    description="MANUAL ONLY: try /careers, /jobs, /carrieres... for companies flagged as "
    "having no careers page. Never part of the weekly job.",
)
def careers_pages(radar: RadarResource) -> MaterializeResult[None]:
    """Manual only: try the usual careers addresses for flagged companies."""
    repo = radar.repository()
    with radar.http() as http, repo.session():
        result = run_find_careers(repo, http, now=datetime.now(UTC))
    found = "\n".join(f"- {f.company.name}: {f.url}" for f in result.found)
    return MaterializeResult(
        metadata={
            "tried": len(result.found) + len(result.not_found),
            "found": len(result.found),
            "found_pages": MetadataValue.md(found or "none"),
        }
    )


WEEKLY_ASSETS = [user_companies, company_scores, scores_csv, open_roles]
