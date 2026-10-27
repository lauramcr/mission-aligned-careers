-- 007: careers pages found by trying the usual addresses (radar companies find-careers).
CREATE TABLE careers_found (
    company_id VARCHAR PRIMARY KEY,
    careers_url VARCHAR NOT NULL,
    found_at   TIMESTAMP NOT NULL
);
