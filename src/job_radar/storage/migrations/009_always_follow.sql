-- 009: your own list's "Always follow" tick (config/companies.csv, column always_follow):
-- ticked (the default, and for every existing row) = followed whatever its score; unticked =
-- the score decides (followed or worth a look, never rejected).
ALTER TABLE company_sources ADD COLUMN always_follow BOOLEAN DEFAULT TRUE;
