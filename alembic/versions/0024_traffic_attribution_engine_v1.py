"""Add conservative Traffic Attribution Engine v1 state."""
from alembic import op
import sqlalchemy as sa

revision = "0024_traffic_engine_v1"
down_revision = "0023_startapp_traffic_poc"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for name, column in [
        ("referer", sa.Column("referer", sa.String(2048))),
        ("user_agent", sa.Column("user_agent", sa.Text())),
        ("landing_at", sa.Column("landing_at", sa.DateTime(timezone=True))),
        ("redirect_at", sa.Column("redirect_at", sa.DateTime(timezone=True))),
        ("status", sa.Column("status", sa.String(24), nullable=False, server_default="LANDING_VISIT")),
        ("campaign_metadata_json", sa.Column("campaign_metadata_json", sa.Text())),
    ]:
        if name not in {item["name"] for item in sa.inspect(bind).get_columns("traffic_visits")}: 
            op.add_column("traffic_visits", column)
    if "ix_traffic_visits_redirect_at" not in {item["name"] for item in sa.inspect(bind).get_indexes("traffic_visits")}: 
        op.create_index("ix_traffic_visits_redirect_at", "traffic_visits", ["redirect_at"])
    op.execute("UPDATE traffic_visits SET landing_at = visited_at WHERE landing_at IS NULL")

    if not sa.inspect(bind).has_table("max_join_events"):
        op.create_table(
        "max_join_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_user_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("added_at", sa.DateTime(timezone=True)),
        sa.Column("raw_event_json", sa.Text()),
        sa.Column("dedupe_key", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("max_join_events")}
    for column in ("channel_id", "max_user_id", "event_type", "requested_at", "added_at", "dedupe_key"):
        name = "ix_max_join_events_" + column
        if name not in indexes:
            op.create_index(name, "max_join_events", [column])


def downgrade():
    op.drop_table("max_join_events")
    op.drop_index("ix_traffic_visits_redirect_at", table_name="traffic_visits")
    for column in ("campaign_metadata_json", "status", "redirect_at", "landing_at", "user_agent", "referer"):
        op.drop_column("traffic_visits", column)
