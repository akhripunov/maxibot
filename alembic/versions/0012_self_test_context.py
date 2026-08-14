"""Add temporary channel-user context for one-account testing."""
from alembic import op
import sqlalchemy as sa

revision = "0012_self_test_context"
down_revision = "0011_legacy_feed_cutover"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admins", sa.Column("self_test_mode", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("admins", sa.Column("self_test_channel_id", sa.String(length=36), nullable=True))
    op.add_column("messages", sa.Column("is_self_test", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("messages", "is_self_test")
    op.drop_column("admins", "self_test_channel_id")
    op.drop_column("admins", "self_test_mode")
