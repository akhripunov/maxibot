"""Store temporary messages-screen UI message ids."""
from alembic import op
import sqlalchemy as sa

revision = "0013_messages_ui_ids"
down_revision = "0012_self_test_context"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admins", sa.Column("messages_header_message_id", sa.String(length=128), nullable=True))
    op.add_column("admins", sa.Column("messages_controls_message_id", sa.String(length=128), nullable=True))


def downgrade():
    op.drop_column("admins", "messages_controls_message_id")
    op.drop_column("admins", "messages_header_message_id")
