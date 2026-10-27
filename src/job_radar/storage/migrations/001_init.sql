-- 001: initial schema. All timestamps are stored as UTC (naive TIMESTAMP).

CREATE TABLE companies (
    company_id    VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    website       VARCHAR NOT NULL,
    country       VARCHAR,
    city          VARCHAR,
    first_seen_at TIMESTAMP NOT NULL,
    last_seen_at  TIMESTAMP NOT NULL
);

-- Where each company was found. source_name 'user_csv' = the user's own list (known).
CREATE TABLE company_sources (
    company_id    VARCHAR NOT NULL,
    source_name   VARCHAR NOT NULL,
    source_url    VARCHAR,
    notes         VARCHAR,
    referral      VARCHAR,
    discovered_at TIMESTAMP NOT NULL,
    last_seen_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (company_id, source_name)
);

-- Mirror of config/companies_pending.csv, replaced on every import.
CREATE TABLE pending_companies (
    name        VARCHAR NOT NULL,
    website     VARCHAR,
    careers_url VARCHAR,
    notes       VARCHAR,
    referral    VARCHAR,
    reason      VARCHAR NOT NULL,
    added_on    DATE NOT NULL
);

CREATE TABLE career_boards (
    company_id  VARCHAR PRIMARY KEY,
    kind        VARCHAR NOT NULL,
    slug        VARCHAR,
    careers_url VARCHAR,
    detected_at TIMESTAMP NOT NULL,
    last_ok_at  TIMESTAMP,
    last_error  VARCHAR
);

CREATE TABLE job_postings (
    job_id        VARCHAR PRIMARY KEY,
    company_id    VARCHAR NOT NULL,
    source        VARCHAR,
    title         VARCHAR NOT NULL,
    url           VARCHAR NOT NULL,
    location      VARCHAR,
    remote        VARCHAR NOT NULL,
    contract      VARCHAR NOT NULL,
    part_time     BOOLEAN,
    office_days   INTEGER,
    seniority     VARCHAR NOT NULL,
    department    VARCHAR,
    language      VARCHAR,
    salary_min    DOUBLE,
    salary_max    DOUBLE,
    currency      VARCHAR,
    salary_period VARCHAR,
    description   VARCHAR,
    published_at  TIMESTAMP,
    first_seen_at TIMESTAMP NOT NULL,
    last_seen_at  TIMESTAMP NOT NULL,
    is_open       BOOLEAN NOT NULL,
    closed_at     TIMESTAMP
);

-- History of careers-page text, one row per fetch that changed (or first fetch).
CREATE TABLE page_snapshots (
    company_id   VARCHAR NOT NULL,
    url          VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL,
    lines        VARCHAR[] NOT NULL,
    fetched_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (url, fetched_at)
);

CREATE TABLE pipeline_runs (
    run_id      VARCHAR PRIMARY KEY,
    pipeline    VARCHAR NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status      VARCHAR NOT NULL,
    counts      VARCHAR,   -- JSON
    errors      VARCHAR    -- JSON
);
