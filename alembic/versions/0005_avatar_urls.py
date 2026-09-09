"""Store MAX channel and user avatar URLs for the CRM."""
from alembic import op
import sqlalchemy as sa

revision = "0005_avatar_urls"
down_revision = "0004_welcome_copy"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("channels", sa.Column("avatar_url", sa.String(2048)))
    op.add_column("conversations", sa.Column("avatar_url", sa.String(2048)))


def downgrade():
    op.drop_column("conversations", "avatar_url")
    op.drop_column("channels", "avatar_url")
