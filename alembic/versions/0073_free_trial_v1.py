"""Add one-user seven-day service-level free trials."""

from alembic import op
import sqlalchemy as sa


revision = "0073_free_trial_v1"
down_revision = "0072_canonical_bot_promotion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "admins",
        sa.Column("pending_free_trial", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "free_trials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("admin_id", sa.String(length=36), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activated_channel_id", sa.String(length=36), sa.ForeignKey("channels.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("trial_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("admin_id", name="uq_free_trials_admin_id"),
    )
    op.create_index("ix_free_trials_admin_id", "free_trials", ["admin_id"])
    op.create_index("ix_free_trials_activated_channel_id", "free_trials", ["activated_channel_id"])
    op.create_index("ix_free_trials_status", "free_trials", ["status"])
    op.create_index("ix_free_trials_trial_end_at", "free_trials", ["trial_end_at"])


def downgrade() -> None:
    op.drop_index("ix_free_trials_trial_end_at", table_name="free_trials")
    op.drop_index("ix_free_trials_status", table_name="free_trials")
    op.drop_index("ix_free_trials_activated_channel_id", table_name="free_trials")
    op.drop_index("ix_free_trials_admin_id", table_name="free_trials")
    op.drop_table("free_trials")
    op.drop_column("admins", "pending_free_trial")
