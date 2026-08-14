"""Add client creation timestamp for owner statistics."""
from alembic import op
import sqlalchemy as sa

revision = "0017_admin_created_at"
down_revision = "0016_support_tickets"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "admins",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_column("admins", "created_at")
