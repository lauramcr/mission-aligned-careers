-- 008: companies flagged as having no careers page known (website read, no careers link, no
-- board, nothing found by trying addresses). A row means "flagged"; last_tried_at records the
-- last `radar companies find-careers` attempt.
CREATE TABLE careers_missing (
    company_id    VARCHAR PRIMARY KEY,
    since         TIMESTAMP NOT NULL,
    last_tried_at TIMESTAMP
);

-- Flag the companies whose website was already read without finding a careers page.
INSERT INTO careers_missing
SELECT t.company_id, t.fetched_at, NULL
FROM company_texts t
WHERE t.careers_url IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM career_boards b WHERE b.company_id = t.company_id AND b.kind <> 'none'
  )
  AND NOT EXISTS (SELECT 1 FROM careers_found f WHERE f.company_id = t.company_id);
