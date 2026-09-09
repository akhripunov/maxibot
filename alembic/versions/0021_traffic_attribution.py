"""Add traffic settings, membership events and conservative attribution state."""
from alembic import op
import sqlalchemy as sa

revision = "0021_traffic_attribution"
down_revision = "0020_traffic_mvp"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("traffic_profiles", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("traffic_profiles", sa.Column("target_url", sa.String(2048)))
    op.add_column("traffic_profiles", sa.Column("channel_mode", sa.String(16), nullable=False, server_default="UNKNOWN"))
    op.add_column("traffic_profiles", sa.Column("attribution_window_hours", sa.Integer(), nullable=False, server_default="72"))
    op.add_column("traffic_profiles", sa.Column("conversion_policy", sa.String(24), nullable=False, server_default="CONSERVATIVE"))
    op.create_table(
        "traffic_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_user_id", sa.String(128), nullable=False),
        sa.Column("current_member", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        sa.Column("attributed_visit_id", sa.String(36), sa.ForeignKey("traffic_visits.id", ondelete="SET NULL")),
        sa.Column("attribution_status", sa.String(24), nullable=False, server_default="UNATTRIBUTED"),
        sa.Column("attribution_score", sa.Integer()),
        sa.Column("source_event_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel_id", "max_user_id"),
    )
    op.create_index("ix_traffic_memberships_channel_id", "traffic_memberships", ["channel_id"])
    op.create_index("ix_traffic_memberships_max_user_id", "traffic_memberships", ["max_user_id"])
    op.create_index("ix_traffic_memberships_attribution_status", "traffic_memberships", ["attribution_status"])
    op.create_table(
        "traffic_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("visit_id", sa.String(36), sa.ForeignKey("traffic_visits.id", ondelete="SET NULL")),
        sa.Column("max_user_id", sa.String(128)),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.Text()),
        sa.Column("dedupe_key", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("channel_id", "visit_id", "max_user_id", "event_type", "dedupe_key"):
        op.create_index("ix_traffic_events_" + col, "traffic_events", [col])
    op.create_table(
        "traffic_attribution_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("membership_id", sa.String(36), sa.ForeignKey("traffic_memberships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("visit_id", sa.String(36), sa.ForeignKey("traffic_visits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("membership_id", "visit_id"),
    )
    op.create_index("ix_traffic_attribution_candidates_membership_id", "traffic_attribution_candidates", ["membership_id"])
    op.create_index("ix_traffic_attribution_candidates_visit_id", "traffic_attribution_candidates", ["visit_id"])
    op.create_table(
        "offline_conversion_queue",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("visit_id", sa.String(36), sa.ForeignKey("traffic_visits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("membership_id", sa.String(36), sa.ForeignKey("traffic_memberships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target", sa.String(32), nullable=False, server_default="YANDEX"),
        sa.Column("identifier_type", sa.String(32)),
        sa.Column("identifier_value", sa.String(255)),
        sa.Column("conversion_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("visit_id", "membership_id", "target"),
    )
    op.create_index("ix_offline_conversion_queue_visit_id", "offline_conversion_queue", ["visit_id"])
    op.create_index("ix_offline_conversion_queue_membership_id", "offline_conversion_queue", ["membership_id"])
    op.create_index("ix_offline_conversion_queue_status", "offline_conversion_queue", ["status"])

def downgrade():
    op.drop_table("offline_conversion_queue")
    op.drop_table("traffic_attribution_candidates")
    op.drop_table("traffic_events")
    op.drop_table("traffic_memberships")
    for col in ("conversion_policy", "attribution_window_hours", "channel_mode", "target_url", "enabled"):
        op.drop_column("traffic_profiles", col)
