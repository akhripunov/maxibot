"""Allow the first real-feed open to backfill legacy incoming messages."""
from alembic import op
import sqlalchemy as sa

revision = "0009_backfill_legacy_feed"
down_revision = "0008_message_attachments"
branch_labels = None
depends_on = None


def upgrade():
    # Messages created before attachments/feed delivery existed have NULL
    # attachments_json and were never emitted as ordinary admin feed items.
    op.execute("UPDATE messages SET admin_feed_delivered = FALSE WHERE direction = 'in' AND attachments_json IS NULL")


def downgrade():
    pass
