from alembic import op

revision = "005_m2_call_attempt"
down_revision = "004_m2_batch"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE m1_call ADD COLUMN attempt TINYINT UNSIGNED NOT NULL DEFAULT 1,
        DROP PRIMARY KEY, ADD PRIMARY KEY (stage_id,route_sha,phase,attempt),
        ADD CHECK (attempt IN (1,2))""")


def downgrade():
    raise RuntimeError("Retained model calls require restoring a backup to downgrade.")
