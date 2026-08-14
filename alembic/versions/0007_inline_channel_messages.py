"""Persist inline channel-message reply state."""
from alembic import op
import sqlalchemy as sa

revision = "0007_inline_channel_messages"
down_revision = "0006_message_image_url"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admins", sa.Column("pending_reply_conversation_id", sa.String(36)))


def downgrade():
    op.drop_column("admins", "pending_reply_conversation_id")
