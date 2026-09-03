"""Persist asynchronous Metrica offline-upload status."""

from alembic import op
import sqlalchemy as sa


revision = "0058_metrica_upload_tracking"
down_revision = "0057_join_intent_check"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("offline_conversion_queue", sa.Column("metrica_upload_id", sa.String(64)))
    op.add_column("offline_conversion_queue", sa.Column("metrica_upload_status", sa.String(32)))
    op.add_column("offline_conversion_queue", sa.Column("metrica_upload_checked_at", sa.DateTime(timezone=True)))
    op.create_index("ix_offline_conversion_queue_metrica_upload_id", "offline_conversion_queue", ["metrica_upload_id"])
    op.create_index("ix_offline_conversion_queue_metrica_upload_status", "offline_conversion_queue", ["metrica_upload_status"])


def downgrade():
    op.drop_index("ix_offline_conversion_queue_metrica_upload_status", table_name="offline_conversion_queue")
    op.drop_index("ix_offline_conversion_queue_metrica_upload_id", table_name="offline_conversion_queue")
    op.drop_column("offline_conversion_queue", "metrica_upload_checked_at")
    op.drop_column("offline_conversion_queue", "metrica_upload_status")
    op.drop_column("offline_conversion_queue", "metrica_upload_id")
