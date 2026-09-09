"""Add independent system MAXIBOT landing visits."""
from alembic import op
import sqlalchemy as sa

revision = "0036_system_traffic_visits"
down_revision = "0035_system_yandex_oauth"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if "system_traffic_visits" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "system_traffic_visits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token", sa.String(96), nullable=False, unique=True),
        sa.Column("yclid", sa.String(255)),
        sa.Column("utm_source", sa.String(255)),
        sa.Column("utm_medium", sa.String(255)),
        sa.Column("utm_campaign", sa.String(255)),
        sa.Column("max_user_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False, server_default="LANDING_VISIT"),
    )
    op.create_index("ix_system_traffic_visits_token", "system_traffic_visits", ["token"], unique=True)
    op.create_index("ix_system_traffic_visits_yclid", "system_traffic_visits", ["yclid"])
    op.create_index("ix_system_traffic_visits_max_user_id", "system_traffic_visits", ["max_user_id"])
    op.create_index("ix_system_traffic_visits_created_at", "system_traffic_visits", ["created_at"])

def downgrade():
    op.drop_table("system_traffic_visits")
