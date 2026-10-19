-- Save attribution is additive. Do not backfill legacy authors from ownership.
CREATE TABLE IF NOT EXISTS version_authors (
    version_id VARCHAR(36) PRIMARY KEY REFERENCES versions(id),
    user_id VARCHAR(80) NOT NULL,
    identity_kind VARCHAR(16) NOT NULL
);
