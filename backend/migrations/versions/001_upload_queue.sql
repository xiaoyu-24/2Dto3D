CREATE TABLE app_user (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  login VARCHAR(160) COLLATE utf8mb4_bin NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  is_admin BOOLEAN NOT NULL DEFAULT FALSE
) ENGINE=InnoDB;

CREATE TABLE user_session (
  token_sha BINARY(32) PRIMARY KEY,
  user_id BIGINT NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  FOREIGN KEY (user_id) REFERENCES app_user(id)
) ENGINE=InnoDB;

CREATE TABLE project (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(160) NOT NULL,
  daily_budget_cny DECIMAL(14,4) NOT NULL DEFAULT 500,
  version_budget_cny DECIMAL(14,4) NOT NULL DEFAULT 10
) ENGINE=InnoDB;

CREATE TABLE project_member (
  project_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  role ENUM('reader','operator','admin') NOT NULL,
  PRIMARY KEY (project_id,user_id),
  KEY member_projects (user_id,project_id),
  FOREIGN KEY (project_id) REFERENCES project(id),
  FOREIGN KEY (user_id) REFERENCES app_user(id)
) ENGINE=InnoDB;

CREATE TABLE file_bytes (
  sha BINARY(32) PRIMARY KEY,
  byte_count BIGINT UNSIGNED NOT NULL,
  body LONGBLOB NOT NULL,
  CHECK (byte_count = OCTET_LENGTH(body))
) ENGINE=InnoDB;

CREATE TABLE upload_batch (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  project_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  request_key VARBINARY(96) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE (project_id,request_key),
  KEY batch_list (project_id,created_at,id),
  FOREIGN KEY (project_id) REFERENCES project(id),
  FOREIGN KEY (user_id) REFERENCES app_user(id)
) ENGINE=InnoDB;

CREATE TABLE source_file (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  batch_id BIGINT NOT NULL,
  client_file_key VARBINARY(96) NOT NULL,
  filename VARCHAR(255) NOT NULL,
  sha BINARY(32) NOT NULL,
  selection_json JSON NOT NULL,
  state ENUM('accepted','rejected') NOT NULL,
  error_text TEXT,
  UNIQUE (batch_id,client_file_key),
  FOREIGN KEY (batch_id) REFERENCES upload_batch(id),
  FOREIGN KEY (sha) REFERENCES file_bytes(sha)
) ENGINE=InnoDB;

CREATE TABLE product_task (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_id BIGINT NOT NULL,
  scope_key VARBINARY(192) NOT NULL,
  state VARCHAR(32) NOT NULL,
  budget_cny DECIMAL(14,4) NOT NULL,
  UNIQUE (source_id,scope_key),
  KEY task_state (source_id,state),
  FOREIGN KEY (source_id) REFERENCES source_file(id)
) ENGINE=InnoDB;

CREATE TABLE task_stage (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id BIGINT NOT NULL,
  stage_name VARCHAR(48) NOT NULL,
  idem_key BINARY(32) NOT NULL UNIQUE,
  input_sha BINARY(32) NOT NULL,
  output_sha BINARY(32),
  state ENUM('ready','running','done','waiting','failed','cancelled','stale') NOT NULL,
  ready_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  owner VARBINARY(96),
  fence BIGINT UNSIGNED NOT NULL DEFAULT 0,
  lease_until DATETIME(6),
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  error_code VARCHAR(64),
  error_text TEXT,
  KEY claim_stage (state,ready_at,id),
  KEY expired_stage (state,lease_until,id),
  KEY task_history (task_id,id),
  FOREIGN KEY (task_id) REFERENCES product_task(id),
  FOREIGN KEY (input_sha) REFERENCES file_bytes(sha),
  FOREIGN KEY (output_sha) REFERENCES file_bytes(sha)
) ENGINE=InnoDB;

CREATE TABLE stage_event (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  stage_id BIGINT NOT NULL,
  fence BIGINT UNSIGNED NOT NULL,
  kind VARCHAR(48) NOT NULL,
  detail JSON NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY stage_timeline (stage_id,id),
  FOREIGN KEY (stage_id) REFERENCES task_stage(id)
) ENGINE=InnoDB;
