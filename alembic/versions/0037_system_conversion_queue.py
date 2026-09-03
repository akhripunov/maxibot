"""Add isolated system bot_started conversion queue."""
from alembic import op
import sqlalchemy as sa

revision = "0037_system_conversion_queue"
down_revision = "0036_system_traffic_visits"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if "system_conversion_queue" in sa.inspect(bind).get_table_names():
        return
    op.create_table("system_conversion_queue",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("visit_id", sa.String(36), sa.ForeignKey("system_traffic_visits.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("max_user_id", sa.String(128), nullable=False),
        sa.Column("goal", sa.String(64), nullable=False, server_default="bot_started"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_system_conversion_queue_visit_id", "system_conversion_queue", ["visit_id"], unique=True)
    op.create_index("ix_system_conversion_queue_max_user_id", "system_conversion_queue", ["max_user_id"])
    op.create_index("ix_system_conversion_queue_status", "system_conversion_queue", ["status"])

def downgrade():
    op.drop_table("system_conversion_queue")
