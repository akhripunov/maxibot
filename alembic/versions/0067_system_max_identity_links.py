"""Add verified identity links for system BOT handoffs."""
from alembic import op
import sqlalchemy as sa


revision = "0067_system_max_identity_links"
down_revision = "0066_shared_bot_handoff"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "system_max_identity_links" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "system_max_identity_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("system_visit_id", sa.String(36), sa.ForeignKey("system_traffic_visits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_user_id", sa.String(128), nullable=False),
        sa.Column("startapp_token", sa.String(96), nullable=False),
        sa.Column("init_data_fingerprint", sa.String(64), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("system_visit_id", name="uq_system_max_identity_visit"),
    )
    op.create_index("ix_system_max_identity_links_system_visit_id", "system_max_identity_links", ["system_visit_id"], unique=True)
    op.create_index("ix_system_max_identity_links_max_user_id", "system_max_identity_links", ["max_user_id"])
    op.create_index("ix_system_max_identity_links_startapp_token", "system_max_identity_links", ["startapp_token"])


def downgrade():
    op.drop_table("system_max_identity_links")
