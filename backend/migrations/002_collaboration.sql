-- 002: additive PostgreSQL DDL; existing documents/versions are never rewritten.

-- Use python -m app.migrate for the explicit legacy-owner data migration.


CREATE TABLE IF NOT EXISTS conversations (
	id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(80) NOT NULL, 
	visibility VARCHAR(12) NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id)
)

;

CREATE INDEX IF NOT EXISTS ix_conversations_document_id ON conversations (document_id);


CREATE TABLE IF NOT EXISTS document_access (
	document_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(80) NOT NULL, 
	role VARCHAR(12) NOT NULL, 
	PRIMARY KEY (document_id, user_id), 
	FOREIGN KEY(document_id) REFERENCES documents (id)
)

;


CREATE TABLE IF NOT EXISTS lease_owners (
	document_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(80) NOT NULL, 
	PRIMARY KEY (document_id), 
	FOREIGN KEY(document_id) REFERENCES documents (id)
)

;


CREATE TABLE IF NOT EXISTS conversation_contexts (
	conversation_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	base_version VARCHAR(36) NOT NULL, 
	content TEXT NOT NULL, 
	updated_at FLOAT NOT NULL, 
	PRIMARY KEY (conversation_id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id), 
	FOREIGN KEY(base_version) REFERENCES versions (id)
)

;


CREATE TABLE IF NOT EXISTS conversation_messages (
	id VARCHAR(36) NOT NULL, 
	conversation_id VARCHAR(36) NOT NULL, 
	author_id VARCHAR(80) NOT NULL, 
	role VARCHAR(12) NOT NULL, 
	content TEXT NOT NULL, 
	source VARCHAR(20) NOT NULL, 
	request_id VARCHAR(64) NOT NULL, 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (conversation_id, request_id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
)

;

CREATE INDEX IF NOT EXISTS ix_conversation_messages_conversation_id ON conversation_messages (conversation_id);


CREATE TABLE IF NOT EXISTS templates (
	id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description VARCHAR(1000) NOT NULL, 
	tags TEXT NOT NULL, 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(version_id) REFERENCES versions (id)
)

;

CREATE INDEX IF NOT EXISTS ix_templates_document_id ON templates (document_id);


CREATE TABLE IF NOT EXISTS text_proposals (
	id VARCHAR(36) NOT NULL, 
	conversation_id VARCHAR(36) NOT NULL, 
	context_revision INTEGER NOT NULL, 
	base_version VARCHAR(36) NOT NULL, 
	snapshot TEXT NOT NULL, 
	replacement TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	author_id VARCHAR(80) NOT NULL, 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id), 
	FOREIGN KEY(base_version) REFERENCES versions (id)
)

;

CREATE INDEX IF NOT EXISTS ix_text_proposals_conversation_id ON text_proposals (conversation_id);


CREATE TABLE IF NOT EXISTS version_changes (
	version_id VARCHAR(36) NOT NULL, 
	base_sha256 VARCHAR(64) NOT NULL, 
	entries TEXT NOT NULL, 
	PRIMARY KEY (version_id), 
	FOREIGN KEY(version_id) REFERENCES versions (id)
)

;
