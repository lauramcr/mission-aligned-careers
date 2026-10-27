-- 005: one row per Claude Code call, to measure usage against the user's plan.
CREATE TABLE llm_calls (
    run_id             VARCHAR,
    called_at          TIMESTAMP NOT NULL,
    purpose            VARCHAR NOT NULL,   -- facts | resolve | names
    subject            VARCHAR NOT NULL,
    model              VARCHAR NOT NULL,
    ok                 BOOLEAN NOT NULL,
    input_tokens       INTEGER NOT NULL,
    output_tokens      INTEGER NOT NULL,
    cache_read_tokens  INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost_usd           DOUBLE NOT NULL,
    duration_ms        INTEGER NOT NULL
);
