"""Persist idempotent daily channel report deliveries."""
from alembic import op
import sqlalchemy as sa


revision = "0029_daily_channel_reports"
down_revision = "0028_offline_conversion_goal"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "daily_channel_reports" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "daily_channel_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("subscriber_count", sa.Integer()),
        sa.Column("subscriber_delta", sa.Integer()),
        sa.Column("subscribed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unsubscribed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invite_link_unsubscribed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("undetermined_unsubscribed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel_id", "report_date", name="uq_daily_channel_report_date"),
    )
    op.create_index("ix_daily_channel_reports_channel_id", "daily_channel_reports", ["channel_id"])
    op.create_index("ix_daily_channel_reports_status", "daily_channel_reports", ["status"])


def downgrade():
    op.drop_table("daily_channel_reports")
