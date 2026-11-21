from alembic import op

revision = "006_m2_report_binding"
down_revision = "005_m2_call_attempt"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""UPDATE m2_version SET step_check=JSON_SET(step_check,'$.report_sha256',LOWER(HEX(report_sha)))
        WHERE state='published' AND report_sha IS NOT NULL AND step_check IS NOT NULL""")


def downgrade():
    raise RuntimeError("Retained report bindings require restoring a backup to downgrade.")
