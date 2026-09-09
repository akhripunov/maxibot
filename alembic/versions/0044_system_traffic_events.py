"""Add system-scoped landing lifecycle telemetry."""
from alembic import op
import sqlalchemy as sa

revision = "0044_system_traffic_events"
down_revision = "0043_bot_public_id"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "system_traffic_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("visit_id", sa.String(length=36), sa.ForeignKey("system_traffic_visits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_system_traffic_events_dedupe_key"),
    )
    op.create_index("ix_system_traffic_events_visit_id", "system_traffic_events", ["visit_id"])
    op.create_index("ix_system_traffic_events_event_type", "system_traffic_events", ["event_type"])
    op.create_index("ix_system_traffic_events_dedupe_key", "system_traffic_events", ["dedupe_key"], unique=True)

def downgrade():
    op.drop_index("ix_system_traffic_events_dedupe_key", table_name="system_traffic_events")
    op.drop_index("ix_system_traffic_events_event_type", table_name="system_traffic_events")
    op.drop_index("ix_system_traffic_events_visit_id", table_name="system_traffic_events")
    op.drop_table("system_traffic_events")
