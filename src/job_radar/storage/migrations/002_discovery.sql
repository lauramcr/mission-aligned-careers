-- 002: discovery — website text, extracted facts, fit assessments.

-- Latest readable text of each company's website (home + about).
CREATE TABLE company_texts (
    company_id   VARCHAR PRIMARY KEY,
    content_hash VARCHAR NOT NULL,
    text         VARCHAR NOT NULL,
    pages        VARCHAR[] NOT NULL,
    careers_url  VARCHAR,
    fetched_at   TIMESTAMP NOT NULL
);

-- LLM-extracted facts; re-extracted only when the text or the extraction version changes.
CREATE TABLE company_facts (
    company_id         VARCHAR NOT NULL,
    extraction_version VARCHAR NOT NULL,
    content_hash       VARCHAR NOT NULL,
    facts              VARCHAR NOT NULL,   -- JSON (CompanyFacts)
    confidence         DOUBLE NOT NULL,
    extracted_at       TIMESTAMP NOT NULL,
    PRIMARY KEY (company_id, extraction_version, content_hash)
);

-- Latest score per company (recomputed whenever criteria or facts change).
CREATE TABLE fit_assessments (
    company_id    VARCHAR PRIMARY KEY,
    profile_hash  VARCHAR NOT NULL,
    impact_fit    DOUBLE NOT NULL,
    practical_fit DOUBLE NOT NULL,
    career_fit    DOUBLE NOT NULL,
    overall       DOUBLE NOT NULL,
    hidden        BOOLEAN NOT NULL,
    needs_review  BOOLEAN NOT NULL,
    flags         VARCHAR[] NOT NULL,
    explanation   VARCHAR NOT NULL,        -- JSON [{component, delta, reason}]
    outcome       VARCHAR NOT NULL,        -- follow | review | skip | hidden | rejected | unscored
    scored_at     TIMESTAMP NOT NULL
);
