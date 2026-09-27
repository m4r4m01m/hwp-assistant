-- 003: additive rewrite tasks, candidates and application ledger.
-- Runtime: Base.metadata.create_all; old records remain unchanged.


CREATE TABLE rewrite_tasks (
	id VARCHAR(36) NOT NULL, 
	conversation_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36) NOT NULL, 
	requested_by VARCHAR(80) NOT NULL, 
	base_version VARCHAR(36) NOT NULL, 
	snapshot TEXT NOT NULL, 
	instruction TEXT NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	execution TEXT NOT NULL, 
	error TEXT NOT NULL, 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(base_version) REFERENCES versions (id)
)

;
CREATE INDEX ix_rewrite_tasks_document_id ON rewrite_tasks (document_id);
CREATE INDEX ix_rewrite_tasks_conversation_id ON rewrite_tasks (conversation_id);

CREATE TABLE rewrite_candidates (
	id VARCHAR(36) NOT NULL, 
	task_id VARCHAR(36) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	replacement TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (task_id, ordinal), 
	FOREIGN KEY(task_id) REFERENCES rewrite_tasks (id)
)

;
CREATE INDEX ix_rewrite_candidates_task_id ON rewrite_candidates (task_id);

CREATE TABLE rewrite_applications (
	id VARCHAR(36) NOT NULL, 
	task_id VARCHAR(36) NOT NULL, 
	candidate_id VARCHAR(36) NOT NULL, 
	approved_by VARCHAR(80) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	receipt TEXT, 
	export_sha256 VARCHAR(64), 
	saved_version VARCHAR(36), 
	created_at FLOAT NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (task_id), 
	FOREIGN KEY(task_id) REFERENCES rewrite_tasks (id), 
	FOREIGN KEY(candidate_id) REFERENCES rewrite_candidates (id), 
	FOREIGN KEY(saved_version) REFERENCES versions (id)
)

;