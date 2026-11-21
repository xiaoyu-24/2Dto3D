from alembic import op

revision = "003_m1_delivery"
down_revision = "002_m1_vision"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE m1_delivery (
        task_id BIGINT PRIMARY KEY,
        basis_sha BINARY(32) NOT NULL,
        checklist_sha BINARY(32) NOT NULL,
        geometry_sha BINARY(32) NOT NULL,
        step_sha BINARY(32) NOT NULL,
        html_sha BINARY(32) NOT NULL,
        report_sha BINARY(32) NOT NULL,
        published_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        FOREIGN KEY (task_id) REFERENCES product_task(id),
        FOREIGN KEY (basis_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (checklist_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (geometry_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (step_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (html_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (report_sha) REFERENCES file_bytes(sha)
    ) ENGINE=InnoDB""")


def downgrade():
    raise RuntimeError("This migration contains published deliveries; restore a backup to downgrade.")
