"""Make custom bots standalone products with their own message history."""
from alembic import op
import sqlalchemy as sa

revision = "0019_standalone_custom_bots"
down_revision = "0018_custom_bots"
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column("admin_feed_messages", "channel_id", nullable=True)
    op.add_column("bot_connections", sa.Column("welcome_text", sa.Text(), nullable=False, server_default="✅ Напишите ваш вопрос — я передам его администратору."))
    op.add_column("bot_connections", sa.Column("sent_text", sa.Text(), nullable=False, server_default="✅ Сообщение отправлено.\nОжидайте ответ."))
    op.create_table(
        "custom_bot_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bot_connection_id", sa.String(36), sa.ForeignKey("bot_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("source_user_id", sa.String(128), nullable=False),
        sa.Column("source_private_chat_id", sa.String(128), nullable=False),
        sa.Column("external_message_id", sa.String(128), unique=True, nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=True),
        sa.Column("reply_to_message_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_custom_bot_messages_bot_connection_id", "custom_bot_messages", ["bot_connection_id"])
    op.create_index("ix_custom_bot_messages_source_user_id", "custom_bot_messages", ["source_user_id"])
    op.create_index("ix_custom_bot_messages_source_private_chat_id", "custom_bot_messages", ["source_private_chat_id"])
    op.create_index("ix_custom_bot_messages_external_message_id", "custom_bot_messages", ["external_message_id"], unique=True)

def downgrade():
    op.drop_table("custom_bot_messages")
    op.drop_column("bot_connections", "sent_text")
    op.drop_column("bot_connections", "welcome_text")
    op.alter_column("admin_feed_messages", "channel_id", nullable=False)
