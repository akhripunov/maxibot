"""Persist whether an administrator has passed the first-run screen."""
from alembic import op
import sqlalchemy as sa

revision = "0045_admin_onboarding_seen"
down_revision = "0044_system_traffic_events"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "admins",
        sa.Column("onboarding_seen", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Existing administrators must keep their established normal-menu flow.
    op.execute(sa.text("UPDATE admins SET onboarding_seen = TRUE"))
    op.alter_column("admins", "onboarding_seen", server_default=None)


def downgrade():
    op.drop_column("admins", "onboarding_seen")
