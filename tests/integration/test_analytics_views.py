"""Analytics views (migration 010): the funnel per source, roles per week, job-board health and
model usage, answered in SQL from the stored tables."""

from typing import Any

import pytest

from job_radar.storage.duckdb_repository import DuckDBRepository

SETUP = """
INSERT INTO companies VALUES
  ('a.com', 'A', 'https://a.com', NULL, NULL, '2026-10-01', '2026-10-20'),
  ('b.com', 'B', 'https://b.com', NULL, NULL, '2026-10-01', '2026-10-20'),
  ('c.com', 'C', 'https://c.com', NULL, NULL, '2026-10-01', '2026-10-20');
INSERT INTO company_sources (company_id, source_name, discovered_at, last_seen_at) VALUES
  ('a.com', 'Fund X', '2026-10-01', '2026-10-20'),
  ('b.com', 'Fund X', '2026-10-01', '2026-10-20'),
  ('c.com', 'my list', '2026-10-01', '2026-10-20');
INSERT INTO fit_assessments (company_id, profile_hash, impact_fit, practical_fit, career_fit,
  overall, hidden, needs_review, flags, explanation, outcome, scored_at) VALUES
  ('a.com', 'p', 4, 4, 4, 4.0, false, false, [], '[]', 'follow', '2026-10-20'),
  ('b.com', 'p', 1, 1, 1, 1.0, false, false, [], '[]', 'rejected', '2026-10-20'),
  ('c.com', 'p', 3, 3, 3, 3.0, false, false, [], '[]', 'review', '2026-10-20');
INSERT INTO career_boards (company_id, kind, slug, careers_url, detected_at, last_ok_at,
  last_error, last_checked_at) VALUES
  ('a.com', 'ashby', 'a', NULL, '2026-10-01', '2026-10-20', NULL, '2026-10-20'),
  ('c.com', 'ashby', 'c', NULL, '2026-10-01', NULL, 'HTTP 404', '2026-10-20');
INSERT INTO job_postings (job_id, company_id, title, url, remote, contract, seniority,
  first_seen_at, last_seen_at, is_open, closed_at) VALUES
  ('ashby:a:1', 'a.com', 'Data engineer', 'u1', 'no', 'permanent', 'mid',
   '2026-10-05', '2026-10-20', true, NULL),
  ('ashby:a:2', 'a.com', 'Analyst', 'u2', 'no', 'permanent', 'mid',
   '2026-10-06', '2026-10-13', false, '2026-10-13'),
  ('ashby:a:3', 'a.com', 'Engineer', 'u3', 'no', 'permanent', 'mid',
   '2026-10-14', '2026-10-20', true, NULL);
INSERT INTO llm_calls VALUES
  ('r1', '2026-10-20', 'facts', 'a.com', 'm', true, 100, 20, 0, 0, 0.01, 900),
  ('r1', '2026-10-20', 'facts', 'b.com', 'm', false, 50, 0, 0, 0, 0.0, 300);
"""


@pytest.fixture
def repo() -> DuckDBRepository:
    repo = DuckDBRepository(":memory:")
    repo.query(SETUP)
    return repo


def rows(repo: DuckDBRepository, sql: str) -> list[dict[str, Any]]:
    columns, values = repo.query(sql)
    return [dict(zip(columns, row, strict=True)) for row in values]


def test_source_funnel(repo: DuckDBRepository) -> None:
    funnel = rows(repo, "SELECT * FROM v_source_funnel ORDER BY source_name")
    assert funnel == [
        {
            "source_name": "Fund X",
            "companies": 2,
            "scored": 2,
            "followed": 1,
            "worth_a_look": 0,
            "rejected": 1,
            "with_job_board": 1,
            "open_roles": 2,
        },
        {
            "source_name": "my list",
            "companies": 1,
            "scored": 1,
            "followed": 0,
            "worth_a_look": 1,
            "rejected": 0,
            "with_job_board": 1,
            "open_roles": 0,
        },
    ]


def test_roles_by_week(repo: DuckDBRepository) -> None:
    weeks = rows(repo, "SELECT week, new_roles, closed_roles FROM v_roles_by_week ORDER BY week")
    assert [(str(w["week"]), w["new_roles"], w["closed_roles"]) for w in weeks] == [
        ("2026-10-05", 2, 0),
        ("2026-10-12", 1, 1),
    ]


def test_board_health(repo: DuckDBRepository) -> None:
    health = rows(repo, "SELECT * FROM v_board_health")
    assert health == [
        {"kind": "ashby", "boards": 2, "failing": 1, "failure_rate": 0.5, "open_roles": 2}
    ]


def test_llm_usage(repo: DuckDBRepository) -> None:
    usage = rows(repo, "SELECT * FROM v_llm_usage")
    assert usage == [
        {
            "run_id": "r1",
            "purpose": "facts",
            "calls": 2,
            "failed": 1,
            "input_tokens": 150,
            "output_tokens": 20,
            "cost_usd": pytest.approx(0.01),
            "avg_seconds": pytest.approx(0.6),
        }
    ]
