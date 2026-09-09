"""initial schema"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("admins",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("service_message_id", sa.String(128)),
    )
    op.create_index("ix_admins_external_id", "admins", ["external_id"], unique=True)
    op.create_table("channels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("admin_id", sa.String(36), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("accepting", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("public_slug", sa.String(64), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("welcome_text", sa.Text(), nullable=False),
        sa.Column("sent_text", sa.Text(), nullable=False),
    )
    op.create_index("ix_channels_admin_id", "channels", ["admin_id"])
    op.create_index("ix_channels_external_id", "channels", ["external_id"])
    op.create_index("ix_channels_public_slug", "channels", ["public_slug"], unique=True)
    op.create_table("blocked_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_external_id", sa.String(128), nullable=False),
        sa.UniqueConstraint("channel_id", "user_external_id"),
    )
    op.create_index("ix_blocked_users_channel_id", "blocked_users", ["channel_id"])
    op.create_index("ix_blocked_users_user_external_id", "blocked_users", ["user_external_id"])
    op.create_table("conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_external_id", sa.String(128), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("unread", sa.Integer(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversations_channel_id", "conversations", ["channel_id"])
    op.create_index("ix_conversations_user_external_id", "conversations", ["user_external_id"])
    op.create_table("messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

def downgrade():
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("blocked_users")
    op.drop_table("channels")
    op.drop_index("ix_admins_external_id", table_name="admins")
    op.drop_table("admins")
