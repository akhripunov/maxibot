"""Add signed startapp identity links and idempotent subscriptions."""
from alembic import op
import sqlalchemy as sa

revision = "0023_startapp_traffic_poc"
down_revision = "0022_traffic_target_state"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    traffic_columns = {column["name"] for column in inspector.get_columns("traffic_visits")}
    if "startapp_token" not in traffic_columns:
        op.add_column("traffic_visits", sa.Column("startapp_token", sa.String(96), nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("traffic_visits")}
    if "ix_traffic_visits_startapp_token" not in indexes:
        op.create_index("ix_traffic_visits_startapp_token", "traffic_visits", ["startapp_token"], unique=True)
    if not sa.inspect(bind).has_table("max_identity_links"):
        op.create_table(
        "max_identity_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("visitor_id", sa.String(36), sa.ForeignKey("traffic_visits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_user_id", sa.String(128), nullable=False),
        sa.Column("startapp_token", sa.String(96), nullable=False),
        sa.Column("user_name", sa.String(255)),
        sa.Column("init_data_fingerprint", sa.String(64), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("visitor_id", name="uq_max_identity_links_visitor_id"),
        sa.UniqueConstraint("visitor_id", "max_user_id", name="uq_max_identity_links_visitor_user"),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("max_identity_links")}
    for column in ("visitor_id", "max_user_id", "startapp_token"):
        name = "ix_max_identity_links_" + column
        if name not in indexes:
            op.create_index(name, "max_identity_links", [column])

    if not sa.inspect(bind).has_table("traffic_subscriptions"):
        op.create_table(
        "traffic_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("visitor_id", sa.String(36), sa.ForeignKey("traffic_visits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_user_id", sa.String(128), nullable=False),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lost_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("visitor_id", "channel_id", name="uq_traffic_subscriptions_visitor_channel"),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("traffic_subscriptions")}
    for column in ("visitor_id", "max_user_id", "channel_id", "status"):
        name = "ix_traffic_subscriptions_" + column
        if name not in indexes:
            op.create_index(name, "traffic_subscriptions", [column])


def downgrade():
    op.drop_table("traffic_subscriptions")
    op.drop_table("max_identity_links")
    op.drop_index("ix_traffic_visits_startapp_token", table_name="traffic_visits")
    op.drop_column("traffic_visits", "startapp_token")
