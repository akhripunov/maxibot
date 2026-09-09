"""Add per-channel daily report delivery toggle."""

from alembic import op
import sqlalchemy as sa


revision = "0061_daily_reports_toggle"
down_revision = "0060_merge_ads_foundation_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column("daily_reports_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("channels", "daily_reports_enabled")
