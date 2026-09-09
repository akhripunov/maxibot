"""Add traffic MVP landing pages, visits and conversion events."""
from alembic import op
import sqlalchemy as sa

revision = "0020_traffic_mvp"
down_revision = "0019_standalone_custom_bots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "landing_pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        sa.Column("cta_text", sa.String(255), nullable=False, server_default="Перейти в канал"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_landing_pages_channel_id", "landing_pages", ["channel_id"])
    op.create_index("ix_landing_pages_public_id", "landing_pages", ["public_id"], unique=True)
    op.create_table(
        "traffic_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("yandex_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("yandex_counter_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_traffic_profiles_channel_id", "traffic_profiles", ["channel_id"], unique=True)
    op.create_table(
        "traffic_visits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("visit_token", sa.String(96), nullable=False, unique=True),
        sa.Column("landing_page_id", sa.String(36), sa.ForeignKey("landing_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("utm_source", sa.String(255)), sa.Column("utm_medium", sa.String(255)),
        sa.Column("utm_campaign", sa.String(255)), sa.Column("utm_content", sa.String(255)),
        sa.Column("utm_term", sa.String(255)), sa.Column("yclid", sa.String(255)),
        sa.Column("metrica_client_id", sa.String(255)), sa.Column("max_user_id", sa.String(128)),
        sa.Column("visited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_opened_at", sa.DateTime(timezone=True)),
        sa.Column("to_channel_at", sa.DateTime(timezone=True)),
        sa.Column("converted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_traffic_visits_visit_token", "traffic_visits", ["visit_token"], unique=True)
    op.create_index("ix_traffic_visits_landing_page_id", "traffic_visits", ["landing_page_id"])
    op.create_index("ix_traffic_visits_channel_id", "traffic_visits", ["channel_id"])
    op.create_index("ix_traffic_visits_max_user_id", "traffic_visits", ["max_user_id"])
    op.create_table(
        "membership_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("visit_id", sa.String(36), sa.ForeignKey("traffic_visits.id", ondelete="SET NULL"), nullable=True),
        sa.Column("landing_page_id", sa.String(36), sa.ForeignKey("landing_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_max_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("visit_id", "channel_id", "user_max_id", "event_type"),
    )
    for column in ("visit_id", "landing_page_id", "channel_id", "user_max_id"):
        op.create_index("ix_membership_events_" + column, "membership_events", [column])


def downgrade():
    op.drop_table("membership_events")
    op.drop_table("traffic_visits")
    op.drop_table("traffic_profiles")
    op.drop_table("landing_pages")
