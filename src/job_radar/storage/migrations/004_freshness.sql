-- 004: freshness — when each board and list was last checked, and which run found each role.

ALTER TABLE career_boards ADD COLUMN last_checked_at TIMESTAMP;
ALTER TABLE job_postings ADD COLUMN found_run_id VARCHAR;

CREATE TABLE list_reads (
    url          VARCHAR PRIMARY KEY,
    name         VARCHAR NOT NULL,
    last_read_at TIMESTAMP NOT NULL
);
