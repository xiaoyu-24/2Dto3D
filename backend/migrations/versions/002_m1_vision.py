from alembic import op

revision = "002_m1_vision"
down_revision = "001_upload_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE m1_call (
        stage_id BIGINT NOT NULL,
        project_id BIGINT NOT NULL,
        route_sha BINARY(32) NOT NULL,
        phase ENUM('probe','read') NOT NULL,
        started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        reserved_cny DECIMAL(14,4) NOT NULL,
        usage_ceiling_cny DECIMAL(14,4),
        result JSON,
        PRIMARY KEY (stage_id,route_sha,phase),
        KEY project_calls (project_id,started_at),
        FOREIGN KEY (stage_id) REFERENCES task_stage(id),
        FOREIGN KEY (project_id) REFERENCES project(id)
    ) ENGINE=InnoDB""")
    op.execute("""CREATE TABLE m1_basis (
        stage_id BIGINT PRIMARY KEY,
        answers JSON NOT NULL,
        basis_sha BINARY(32),
        checklist_sha BINARY(32),
        FOREIGN KEY (stage_id) REFERENCES task_stage(id),
        FOREIGN KEY (basis_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (checklist_sha) REFERENCES file_bytes(sha)
    ) ENGINE=InnoDB""")


def downgrade():
    raise RuntimeError("This migration contains retained evidence and confirmations; restore a backup to downgrade.")
