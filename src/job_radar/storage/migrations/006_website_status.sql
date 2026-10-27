-- 006: per company, the last attempt to read its website and why it failed (if it did).
CREATE TABLE website_status (
    company_id      VARCHAR PRIMARY KEY,
    last_attempt_at TIMESTAMP NOT NULL,
    ok              BOOLEAN NOT NULL,
    reason          VARCHAR
);
