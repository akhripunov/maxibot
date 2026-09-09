"""Persist original MAX attachments and admin feed delivery state."""
from alembic import op
import sqlalchemy as sa

revision = "0008_message_attachments"
down_revision = "0007_inline_channel_messages"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("attachments_json", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("admin_feed_delivered", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Existing messages predate the separate admin feed and must never be
    # replayed when the administrator opens the Messages button.
    op.execute("UPDATE messages SET admin_feed_delivered = TRUE")


def downgrade():
    op.drop_column("messages", "admin_feed_delivered")
    op.drop_column("messages", "attachments_json")
