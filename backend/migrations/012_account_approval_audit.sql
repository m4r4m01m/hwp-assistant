-- Additive PostgreSQL reference DDL. app.schema.migrate is the supported migration.
-- Preserve documents, passwords, identity ownership, and historical versions.
-- Existing accounts remain approved members; no administrator is inferred.


ALTER TABLE user_accounts ADD COLUMN IF NOT EXISTS role VARCHAR(12) NOT NULL DEFAULT 'member';

ALTER TABLE user_accounts ADD COLUMN IF NOT EXISTS approval_status VARCHAR(12) NOT NULL DEFAULT 'approved';

ALTER TABLE user_accounts ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(80);

ALTER TABLE user_accounts ADD COLUMN IF NOT EXISTS reviewed_at DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS audit_events (
	id SERIAL NOT NULL,
	created_at FLOAT NOT NULL,
	actor_id VARCHAR(80) NOT NULL,
	action VARCHAR(60) NOT NULL,
	target_type VARCHAR(40) NOT NULL,
	target_id VARCHAR(100),
	document_id VARCHAR(36),
	document_name VARCHAR(240),
	version_id VARCHAR(36),
	details TEXT NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_audit_events_action ON audit_events (action);

CREATE INDEX IF NOT EXISTS ix_audit_events_actor_id ON audit_events (actor_id);

CREATE INDEX IF NOT EXISTS ix_audit_events_created_at ON audit_events (created_at);

CREATE INDEX IF NOT EXISTS ix_audit_events_document_id ON audit_events (document_id);

CREATE TABLE IF NOT EXISTS account_settings (
	key VARCHAR(60) NOT NULL,
	value VARCHAR(40) NOT NULL,
	updated_by VARCHAR(80) NOT NULL,
	updated_at FLOAT NOT NULL,
	PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS registration_notifications (
	user_id VARCHAR(80) NOT NULL,
	status VARCHAR(16) NOT NULL,
	attempts INTEGER NOT NULL,
	created_at FLOAT NOT NULL,
	next_attempt_at FLOAT NOT NULL,
	delivered_at FLOAT,
	outcome VARCHAR(20) NOT NULL,
	PRIMARY KEY (user_id)
);

CREATE INDEX IF NOT EXISTS ix_registration_notifications_status ON registration_notifications (status);
