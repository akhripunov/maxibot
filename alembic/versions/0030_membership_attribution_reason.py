"""Persist safe reasons for non-exact membership attribution."""
from alembic import op
import sqlalchemy as sa


revision = "0030_attr_reason"
down_revision = "0029_daily_channel_reports"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("traffic_memberships")}
    if "attribution_reason" not in columns:
        op.add_column(
            "traffic_memberships",
            sa.Column("attribution_reason", sa.String(64), nullable=False, server_default="UNATTRIBUTED"),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("traffic_memberships")}
    if "ix_traffic_memberships_attribution_reason" not in indexes:
        op.create_index("ix_traffic_memberships_attribution_reason", "traffic_memberships", ["attribution_reason"])


def downgrade():
    op.drop_index("ix_traffic_memberships_attribution_reason", table_name="traffic_memberships")
    op.drop_column("traffic_memberships", "attribution_reason")
