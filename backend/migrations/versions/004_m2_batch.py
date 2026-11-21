from alembic import op

revision = "004_m2_batch"
down_revision = "003_m1_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE m1_call MODIFY phase ENUM('probe','read','cad_select') NOT NULL")
    op.execute("""CREATE TABLE m2_product (
        task_id BIGINT PRIMARY KEY,
        part_number VARCHAR(160) NOT NULL,
        specification JSON NOT NULL,
        FOREIGN KEY (task_id) REFERENCES product_task(id)
    ) ENGINE=InnoDB""")
    op.execute("""CREATE TABLE m2_confirmation (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        task_id BIGINT NOT NULL,
        question_key VARCHAR(64) NOT NULL,
        question TEXT NOT NULL,
        evidence JSON NOT NULL,
        answer JSON,
        answered_by BIGINT,
        answered_at DATETIME(6),
        UNIQUE (task_id,question_key),
        FOREIGN KEY (task_id) REFERENCES product_task(id),
        FOREIGN KEY (answered_by) REFERENCES app_user(id)
    ) ENGINE=InnoDB""")
    op.execute("""CREATE TABLE m2_version (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        task_id BIGINT NOT NULL UNIQUE,
        project_id BIGINT NOT NULL,
        part_number VARCHAR(160) NOT NULL,
        version VARCHAR(192) NOT NULL,
        geometry_revision VARCHAR(32) NOT NULL DEFAULT 'G1',
        basis_sha BINARY(32) NOT NULL,
        checklist_sha BINARY(32) NOT NULL,
        geometry_sha BINARY(32),
        snapshot_sha BINARY(32),
        html_sha BINARY(32),
        step_sha BINARY(32),
        report_sha BINARY(32),
        html_check JSON,
        step_check JSON,
        state VARCHAR(32) NOT NULL DEFAULT 'building',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        published_at DATETIME(6),
        UNIQUE (project_id,part_number,version),
        KEY version_search (project_id,part_number,created_at),
        FOREIGN KEY (task_id) REFERENCES product_task(id),
        FOREIGN KEY (project_id) REFERENCES project(id),
        FOREIGN KEY (basis_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (checklist_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (geometry_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (snapshot_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (html_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (step_sha) REFERENCES file_bytes(sha),
        FOREIGN KEY (report_sha) REFERENCES file_bytes(sha)
    ) ENGINE=InnoDB""")
    op.execute("""INSERT INTO m2_version
        (task_id,project_id,part_number,version,basis_sha,checklist_sha,geometry_sha,
         html_sha,step_sha,report_sha,html_check,step_check,state,published_at)
        SELECT d.task_id,b.project_id,'M12A04-03-301-M16',
        IF(ROW_NUMBER() OVER (PARTITION BY b.project_id ORDER BY d.task_id)=1, 'A0-M1.3', CONCAT('A0-M1.3-',d.task_id)),
        d.basis_sha,d.checklist_sha,
        d.geometry_sha,d.html_sha,d.step_sha,d.report_sha,
        JSON_OBJECT('passed',true,'legacy_m1',true,'geometry_sha256',LOWER(HEX(d.geometry_sha))),
        JSON_OBJECT('passed',true,'legacy_m1',true,'geometry_sha256',LOWER(HEX(d.geometry_sha))),
        'published',d.published_at FROM m1_delivery d JOIN product_task t ON t.id=d.task_id
        JOIN source_file s ON s.id=t.source_id JOIN upload_batch b ON b.id=s.batch_id""")


def downgrade():
    raise RuntimeError("Retained deliveries and confirmations require restoring a backup to downgrade.")
