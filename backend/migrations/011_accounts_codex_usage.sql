-- Reference DDL for operators. app.schema performs additive create_all.
-- No existing owner IDs, documents, versions or historical blobs are rewritten.
CREATE TABLE IF NOT EXISTS user_accounts (
    user_id VARCHAR(80) PRIMARY KEY,
    password_hash VARCHAR(256) NOT NULL,
    name VARCHAR(80) NOT NULL,
    employee_number VARCHAR(32) UNIQUE,
    kind VARCHAR(12) NOT NULL,
    is_active BOOLEAN NOT NULL,
    session_revision VARCHAR(64) NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    last_login_at DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS codex_usage (
    request_key VARCHAR(100) PRIMARY KEY,
    user_id VARCHAR(80) NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_codex_usage_created_at ON codex_usage (created_at);
