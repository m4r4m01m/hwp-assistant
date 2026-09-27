-- Initial PostgreSQL schema. Apply once to an empty database.
CREATE TABLE documents (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(240) NOT NULL, 
	current_version VARCHAR(36), 
	created_at FLOAT NOT NULL, 
	lease_token VARCHAR(64), 
	lease_until FLOAT NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE versions (
	id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	base_version VARCHAR(36), 
	storage_key VARCHAR(100) NOT NULL, 
	size INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	created_at FLOAT NOT NULL, 
	cause VARCHAR(20) NOT NULL, 
	request_id VARCHAR(64) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	session_hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (document_id, request_id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	UNIQUE (storage_key)
);

CREATE INDEX ix_versions_document_id ON versions (document_id);
