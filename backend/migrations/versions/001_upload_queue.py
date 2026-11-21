from pathlib import Path

from alembic import op

revision = "001_upload_queue"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError("This migration contains retained source PDFs; restore a backup to downgrade.")
