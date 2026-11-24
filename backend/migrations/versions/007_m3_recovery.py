from alembic import op

revision = "007_m3_recovery"
down_revision = "006_m2_report_binding"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE m1_call ADD COLUMN finished_at DATETIME(6)")
    op.execute("""CREATE TABLE m3_route (
        project_id BIGINT NOT NULL,
        route_sha BINARY(32) NOT NULL,
        disabled BOOLEAN NOT NULL DEFAULT FALSE,
        cooldown_until DATETIME(6),
        probe_until DATETIME(6),
        reason VARCHAR(160),
        PRIMARY KEY (project_id,route_sha),
        FOREIGN KEY (project_id) REFERENCES project(id)
    ) ENGINE=InnoDB""")
    op.execute("""CREATE TABLE m3_publication (
        version_id BIGINT NOT NULL,
        geometry_revision VARCHAR(32) NOT NULL,
        stage_id BIGINT NOT NULL,
        fence BIGINT NOT NULL,
        geometry_sha BINARY(32) NOT NULL,
        html_sha BINARY(32) NOT NULL,
        step_sha BINARY(32) NOT NULL,
        report_sha BINARY(32) NOT NULL,
        published_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (version_id,geometry_revision),
        FOREIGN KEY (version_id) REFERENCES m2_version(id),
        FOREIGN KEY (stage_id) REFERENCES task_stage(id)
    ) ENGINE=InnoDB""")
    op.execute("""CREATE TABLE m3_revision (
        version_id BIGINT NOT NULL,
        geometry_revision VARCHAR(32) NOT NULL,
        evidence JSON NOT NULL,
        recorded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (version_id,geometry_revision),
        FOREIGN KEY (version_id) REFERENCES m2_version(id)
    ) ENGINE=InnoDB""")


def downgrade():
    raise RuntimeError("Recovery audit and revision history require restoring a backup to downgrade.")
