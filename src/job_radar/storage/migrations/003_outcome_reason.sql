-- 003: keep the reason behind every follow / review / rejected outcome.
ALTER TABLE fit_assessments ADD COLUMN outcome_reason VARCHAR DEFAULT '';
