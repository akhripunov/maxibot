"""Add per-channel MAX inbox chat binding."""
from alembic import op
import sqlalchemy as sa

revision = "0014_channel_inbox_chat"
down_revision = "0013_messages_ui_ids"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admins", sa.Column("pending_inbox_channel_id", sa.String(length=36), nullable=True))
    op.add_column("channels", sa.Column("inbox_chat_id", sa.String(length=128), nullable=True))
    op.add_column("channels", sa.Column("inbox_chat_title", sa.String(length=255), nullable=True))
    op.add_column("channels", sa.Column("inbox_chat_connected_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_channels_inbox_chat_id", "channels", ["inbox_chat_id"], unique=False)


def downgrade():
    op.drop_index("ix_channels_inbox_chat_id", table_name="channels")
    op.drop_column("channels", "inbox_chat_connected_at")
    op.drop_column("channels", "inbox_chat_title")
    op.drop_column("channels", "inbox_chat_id")
    op.drop_column("admins", "pending_inbox_channel_id")
