-- Analytics views: questions about the pipeline answered in SQL, e.g.
--   uv run radar db query "SELECT * FROM v_source_funnel"

-- Which lists bring good companies? From each source to scored, followed and open roles.
CREATE VIEW v_source_funnel AS
WITH open_roles AS (
    SELECT company_id, count(*) AS n FROM job_postings WHERE is_open GROUP BY company_id
)
SELECT
    s.source_name,
    count(*)                                              AS companies,
    count(a.company_id)                                   AS scored,
    count(*) FILTER (WHERE a.outcome = 'follow')          AS followed,
    count(*) FILTER (WHERE a.outcome = 'review')          AS worth_a_look,
    count(*) FILTER (WHERE a.outcome = 'rejected')        AS rejected,
    count(b.company_id)                                   AS with_job_board,
    coalesce(sum(r.n), 0)::BIGINT                         AS open_roles
FROM company_sources s
LEFT JOIN fit_assessments a USING (company_id)
LEFT JOIN career_boards b USING (company_id)
LEFT JOIN open_roles r USING (company_id)
GROUP BY s.source_name;

-- How fast do roles appear and close? New and closed roles per week (weeks start on Monday).
CREATE VIEW v_roles_by_week AS
WITH events AS (
    SELECT date_trunc('week', first_seen_at)::DATE AS week, 1 AS new, 0 AS closed
    FROM job_postings
    UNION ALL
    SELECT date_trunc('week', closed_at)::DATE, 0, 1
    FROM job_postings WHERE closed_at IS NOT NULL
)
SELECT week, sum(new)::BIGINT AS new_roles, sum(closed)::BIGINT AS closed_roles
FROM events
GROUP BY week;

-- Which job-board types fail? Boards per type, how many failed last time, and their open roles.
CREATE VIEW v_board_health AS
SELECT
    b.kind,
    count(*)                                              AS boards,
    count(*) FILTER (WHERE b.last_error IS NOT NULL)      AS failing,
    round(count(*) FILTER (WHERE b.last_error IS NOT NULL) / count(*), 2) AS failure_rate,
    (SELECT count(*) FROM job_postings j
     JOIN career_boards b2 USING (company_id)
     WHERE j.is_open AND b2.kind = b.kind)                AS open_roles
FROM career_boards b
GROUP BY b.kind;

-- What did the model cost? Calls, failures, tokens and time per run and purpose.
CREATE VIEW v_llm_usage AS
SELECT
    run_id,
    purpose,
    count(*)                                              AS calls,
    count(*) FILTER (WHERE NOT ok)                        AS failed,
    sum(input_tokens)::BIGINT                             AS input_tokens,
    sum(output_tokens)::BIGINT                            AS output_tokens,
    round(sum(cost_usd), 4)                               AS cost_usd,
    round(avg(duration_ms) / 1000, 2)                     AS avg_seconds
FROM llm_calls
GROUP BY run_id, purpose;
