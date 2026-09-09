"""Add generic promoted-BOT attribution tables without deleting legacy data."""
from alembic import op
import sqlalchemy as sa


revision = "0068_generic_bot_traffic"
down_revision = "0067_system_max_identity_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "bot_traffic_visits" not in tables:
        op.create_table(
            "bot_traffic_visits",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("token", sa.String(96), nullable=False, unique=True),
            sa.Column("bot_id", sa.String(128), nullable=False),
            sa.Column("destination_id", sa.String(128), nullable=False),
            sa.Column("yclid", sa.String(255)),
            sa.Column("utm_source", sa.String(255)), sa.Column("utm_medium", sa.String(255)),
            sa.Column("utm_campaign", sa.String(255)), sa.Column("utm_content", sa.String(255)),
            sa.Column("utm_term", sa.String(255)), sa.Column("metrica_client_id", sa.String(255)),
            sa.Column("referer", sa.String(2048)), sa.Column("user_agent", sa.Text()),
            sa.Column("max_user_id", sa.String(128)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(24), nullable=False, server_default="LANDING_VISIT"),
        )
        op.create_index("ix_bot_traffic_visits_token", "bot_traffic_visits", ["token"], unique=True)
        op.create_index("ix_bot_traffic_visits_bot_id", "bot_traffic_visits", ["bot_id"])
        op.create_index("ix_bot_traffic_visits_destination_id", "bot_traffic_visits", ["destination_id"])
        op.create_index("ix_bot_traffic_visits_yclid", "bot_traffic_visits", ["yclid"])
        op.create_index("ix_bot_traffic_visits_max_user_id", "bot_traffic_visits", ["max_user_id"])
        op.create_index("ix_bot_traffic_visits_created_at", "bot_traffic_visits", ["created_at"])
    if "bot_max_identity_links" not in tables:
        op.create_table(
            "bot_max_identity_links",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("bot_visit_id", sa.String(36), sa.ForeignKey("bot_traffic_visits.id", ondelete="CASCADE"), nullable=False),
            sa.Column("bot_id", sa.String(128), nullable=False),
            sa.Column("destination_id", sa.String(128), nullable=False),
            sa.Column("max_user_id", sa.String(128), nullable=False),
            sa.Column("startapp_token", sa.String(96), nullable=False),
            sa.Column("init_data_fingerprint", sa.String(64), nullable=False),
            sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("bot_visit_id", name="uq_bot_identity_visit"),
        )
        op.create_index("ix_bot_max_identity_links_bot_visit_id", "bot_max_identity_links", ["bot_visit_id"], unique=True)
        op.create_index("ix_bot_max_identity_links_bot_id", "bot_max_identity_links", ["bot_id"])
        op.create_index("ix_bot_max_identity_links_destination_id", "bot_max_identity_links", ["destination_id"])
        op.create_index("ix_bot_max_identity_links_max_user_id", "bot_max_identity_links", ["max_user_id"])
        op.create_index("ix_bot_max_identity_links_startapp_token", "bot_max_identity_links", ["startapp_token"])
    if "bot_traffic_events" not in tables:
        op.create_table(
            "bot_traffic_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("bot_visit_id", sa.String(36), sa.ForeignKey("bot_traffic_visits.id", ondelete="CASCADE"), nullable=False),
            sa.Column("bot_id", sa.String(128), nullable=False),
            sa.Column("destination_id", sa.String(128), nullable=False),
            sa.Column("event_type", sa.String(48), nullable=False),
            sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("metadata_json", sa.Text()),
            sa.Column("dedupe_key", sa.String(255), nullable=False, unique=True),
        )
        op.create_index("ix_bot_traffic_events_bot_visit_id", "bot_traffic_events", ["bot_visit_id"])
        op.create_index("ix_bot_traffic_events_bot_id", "bot_traffic_events", ["bot_id"])
        op.create_index("ix_bot_traffic_events_destination_id", "bot_traffic_events", ["destination_id"])
        op.create_index("ix_bot_traffic_events_event_type", "bot_traffic_events", ["event_type"])
        op.create_index("ix_bot_traffic_events_dedupe_key", "bot_traffic_events", ["dedupe_key"], unique=True)
    if "bot_conversion_queue" not in tables:
        op.create_table(
            "bot_conversion_queue",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("bot_visit_id", sa.String(36), sa.ForeignKey("bot_traffic_visits.id", ondelete="CASCADE"), nullable=False),
            sa.Column("bot_id", sa.String(128), nullable=False),
            sa.Column("destination_id", sa.String(128), nullable=False),
            sa.Column("max_user_id", sa.String(128), nullable=False),
            sa.Column("internal_event", sa.String(64), nullable=False, server_default="bot_started"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("bot_visit_id", "internal_event", name="uq_bot_conversion_visit_event"),
        )
        op.create_index("ix_bot_conversion_queue_bot_visit_id", "bot_conversion_queue", ["bot_visit_id"])
        op.create_index("ix_bot_conversion_queue_bot_id", "bot_conversion_queue", ["bot_id"])
        op.create_index("ix_bot_conversion_queue_destination_id", "bot_conversion_queue", ["destination_id"])
        op.create_index("ix_bot_conversion_queue_max_user_id", "bot_conversion_queue", ["max_user_id"])
        op.create_index("ix_bot_conversion_queue_internal_event", "bot_conversion_queue", ["internal_event"])
        op.create_index("ix_bot_conversion_queue_status", "bot_conversion_queue", ["status"])

    # Preserve already persisted system data. IDs are retained so historical
    # reports and audit references remain stable; legacy tables are untouched.
    op.execute(sa.text("""
        INSERT INTO bot_traffic_visits
            (id, token, bot_id, destination_id, yclid, utm_source, utm_medium,
             utm_campaign, utm_content, utm_term, metrica_client_id, referer,
             user_agent, max_user_id, created_at, started_at, status)
        SELECT id, token, 'maxibot', 'maxibot', yclid, utm_source, utm_medium,
               utm_campaign, utm_content, utm_term, metrica_client_id, referer,
               user_agent, max_user_id, created_at, started_at, status
        FROM system_traffic_visits s
        WHERE NOT EXISTS (SELECT 1 FROM bot_traffic_visits b WHERE b.id = s.id)
    """))
    op.execute(sa.text("""
        INSERT INTO bot_max_identity_links
            (id, bot_visit_id, bot_id, destination_id, max_user_id, startapp_token,
             init_data_fingerprint, linked_at)
        SELECT id, system_visit_id, 'maxibot', 'maxibot', max_user_id, startapp_token,
               init_data_fingerprint, linked_at
        FROM system_max_identity_links l
        WHERE NOT EXISTS (SELECT 1 FROM bot_max_identity_links b WHERE b.id = l.id)
    """))
    op.execute(sa.text("""
        INSERT INTO bot_conversion_queue
            (id, bot_visit_id, bot_id, destination_id, max_user_id, internal_event, status, created_at)
        SELECT id, visit_id, 'maxibot', 'maxibot', max_user_id, goal, status, created_at
        FROM system_conversion_queue c
        WHERE NOT EXISTS (SELECT 1 FROM bot_conversion_queue b WHERE b.id = c.id)
    """))


def downgrade() -> None:
    # Data-preserving rollback: remove only the additive generic tables. The
    # legacy system tables remain available and are never dropped here.
    op.drop_table("bot_conversion_queue")
    op.drop_table("bot_traffic_events")
    op.drop_table("bot_max_identity_links")
    op.drop_table("bot_traffic_visits")
