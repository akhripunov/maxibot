"""Persist the original Metrica counter and bounded recheck state."""

from alembic import op
import sqlalchemy as sa


revision = "0099_metrica_upload_reconciliation_v1"
down_revision = "0098_durable_autopilot_execution_claim_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offline_conversion_queue", sa.Column("metrica_upload_counter_id", sa.String(length=32), nullable=True))
    op.add_column("offline_conversion_queue", sa.Column("metrica_upload_observation_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "offline_conversion_queue",
        sa.Column("metrica_upload_check_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("offline_conversion_queue", sa.Column("metrica_upload_next_check_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("offline_conversion_queue", sa.Column("metrica_upload_check_error", sa.String(length=64), nullable=True))
    op.add_column("offline_conversion_queue", sa.Column("metrica_upload_recovered_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_offline_conversion_queue_metrica_poll",
        "offline_conversion_queue",
        ["status", "metrica_upload_next_check_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_offline_conversion_queue_metrica_poll", table_name="offline_conversion_queue")
    op.drop_column("offline_conversion_queue", "metrica_upload_recovered_at")
    op.drop_column("offline_conversion_queue", "metrica_upload_check_error")
    op.drop_column("offline_conversion_queue", "metrica_upload_next_check_at")
    op.drop_column("offline_conversion_queue", "metrica_upload_check_attempts")
    op.drop_column("offline_conversion_queue", "metrica_upload_observation_started_at")
    op.drop_column("offline_conversion_queue", "metrica_upload_counter_id")
