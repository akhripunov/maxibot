"""Store images attached by CRM administrators."""
from alembic import op
import sqlalchemy as sa

revision = "0006_message_image_url"
down_revision = "0005_avatar_urls"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("image_url", sa.String(2048)))


def downgrade():
    op.drop_column("messages", "image_url")
