"""Add isolated custom bot connections and channel transport metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0018_custom_bots"
down_revision = "0017_admin_created_at"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("admins", sa.Column("custom_bot_state", sa.String(32), nullable=True))
    op.add_column("admins", sa.Column("pending_custom_bot_id", sa.String(36), nullable=True))
    op.add_column("channels", sa.Column("communication_mode", sa.String(20), nullable=False, server_default="SYSTEM_INBOX"))
    op.add_column("channels", sa.Column("bot_connection_id", sa.String(36), nullable=True))
    op.create_index("ix_channels_bot_connection_id", "channels", ["bot_connection_id"])
    op.add_column("admin_feed_messages", sa.Column("source_private_chat_id", sa.String(128), nullable=True))
    op.add_column("admin_feed_messages", sa.Column("source_bot_connection_id", sa.String(36), nullable=True))
    op.add_column("admin_feed_messages", sa.Column("source_transport", sa.String(20), nullable=False, server_default="SYSTEM_INBOX"))
    op.add_column("admin_feed_messages", sa.Column("destination_admin_chat_id", sa.String(128), nullable=True))
    for name, table, column in (("source_private_chat_id","admin_feed_messages","source_private_chat_id"),("source_bot_connection_id","admin_feed_messages","source_bot_connection_id"),("destination_admin_chat_id","admin_feed_messages","destination_admin_chat_id")):
        op.create_index("ix_admin_feed_messages_" + name, table, [column])
    op.create_table(
        "bot_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_admin_id", sa.String(36), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_max_user_id", sa.String(128), nullable=False),
        sa.Column("max_bot_id", sa.String(128), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="WAITING_ADMIN_START"),
        sa.Column("admin_private_chat_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bot_connections_owner_admin_id", "bot_connections", ["owner_admin_id"])
    op.create_index("ix_bot_connections_owner_max_user_id", "bot_connections", ["owner_max_user_id"])
    op.create_index("ix_bot_connections_max_bot_id", "bot_connections", ["max_bot_id"])
    op.create_index("ix_bot_connections_status", "bot_connections", ["status"])
    op.create_index("ix_bot_connections_admin_private_chat_id", "bot_connections", ["admin_private_chat_id"])
    op.create_table(
        "custom_bot_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bot_connection_id", sa.String(36), sa.ForeignKey("bot_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_external_id", sa.String(128), nullable=False),
        sa.Column("private_chat_id", sa.String(128), nullable=False),
        sa.Column("current_channel_id", sa.String(36), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bot_connection_id", "user_external_id"),
    )
    op.create_index("ix_custom_bot_sessions_bot_connection_id", "custom_bot_sessions", ["bot_connection_id"])
    op.create_index("ix_custom_bot_sessions_user_external_id", "custom_bot_sessions", ["user_external_id"])
    op.create_index("ix_custom_bot_sessions_current_channel_id", "custom_bot_sessions", ["current_channel_id"])

def downgrade():
    op.drop_table("custom_bot_sessions")
    op.drop_table("bot_connections")
    for name in ("source_private_chat_id","source_bot_connection_id","destination_admin_chat_id"):
        op.drop_index("ix_admin_feed_messages_" + name, table_name="admin_feed_messages")
    op.drop_column("admin_feed_messages", "destination_admin_chat_id")
    op.drop_column("admin_feed_messages", "source_transport")
    op.drop_column("admin_feed_messages", "source_bot_connection_id")
    op.drop_column("admin_feed_messages", "source_private_chat_id")
    op.drop_index("ix_channels_bot_connection_id", table_name="channels")
    op.drop_column("channels", "bot_connection_id")
    op.drop_column("channels", "communication_mode")
    op.drop_column("admins", "pending_custom_bot_id")
    op.drop_column("admins", "custom_bot_state")
