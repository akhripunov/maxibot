"""native admin feed mappings and feed state

Revision ID: 0010_native_admin_feed
Revises: 0009_backfill_legacy_feed
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_native_admin_feed"
down_revision = "0009_backfill_legacy_feed"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admins", sa.Column("active_feed_channel_id", sa.String(length=36), nullable=True))
    op.add_column("admins", sa.Column("feed_session_id", sa.String(length=36), nullable=True))
    op.add_column("messages", sa.Column("reply_to_message_id", sa.String(length=128), nullable=True))
    op.create_table(
        "admin_feed_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("admin_feed_message_id", sa.String(length=128), nullable=False),
        sa.Column("source_message_id", sa.String(length=36), nullable=False),
        sa.Column("source_external_message_id", sa.String(length=128), nullable=True),
        sa.Column("source_user_id", sa.String(length=128), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("feed_session_id", sa.String(length=36), nullable=False),
    )
    op.create_index("ix_admin_feed_messages_admin_feed_message_id", "admin_feed_messages", ["admin_feed_message_id"], unique=True)
    op.create_index("ix_admin_feed_messages_source_message_id", "admin_feed_messages", ["source_message_id"], unique=True)
    op.create_index("ix_admin_feed_messages_source_external_message_id", "admin_feed_messages", ["source_external_message_id"])
    op.create_index("ix_admin_feed_messages_source_user_id", "admin_feed_messages", ["source_user_id"])
    op.create_index("ix_admin_feed_messages_channel_id", "admin_feed_messages", ["channel_id"])
    op.create_index("ix_admin_feed_messages_feed_session_id", "admin_feed_messages", ["feed_session_id"])


def downgrade():
    op.drop_table("admin_feed_messages")
    op.drop_column("messages", "reply_to_message_id")
    op.drop_column("admins", "feed_session_id")
    op.drop_column("admins", "active_feed_channel_id")
