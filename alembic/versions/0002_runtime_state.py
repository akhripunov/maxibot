"""Persist template input state and MAX message ids for idempotency."""
from alembic import op
import sqlalchemy as sa

revision = "0002_runtime_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admins", sa.Column("pending_template_channel_id", sa.String(36)))
    op.add_column("admins", sa.Column("pending_template_type", sa.String(16)))
    op.add_column("messages", sa.Column("external_message_id", sa.String(128)))
    op.create_index("ix_messages_external_message_id", "messages", ["external_message_id"], unique=True)


def downgrade():
    op.drop_index("ix_messages_external_message_id", table_name="messages")
    op.drop_column("messages", "external_message_id")
    op.drop_column("admins", "pending_template_type")
    op.drop_column("admins", "pending_template_channel_id")
