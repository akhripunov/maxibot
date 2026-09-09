"""Persist sanitized MAX chat metadata needed by traffic discovery."""
from alembic import op
import sqlalchemy as sa

revision = "0026_max_chat_metadata"
down_revision = "0025_auto_approve_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("channels")}
    if "max_owner_user_id" not in columns:
        op.add_column("channels", sa.Column("max_owner_user_id", sa.String(128), nullable=True))
    if "max_chat_metadata_json" not in columns:
        op.add_column("channels", sa.Column("max_chat_metadata_json", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("channels", "max_chat_metadata_json")
    op.drop_column("channels", "max_owner_user_id")
