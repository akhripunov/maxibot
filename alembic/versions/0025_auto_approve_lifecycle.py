"""Add disabled-by-default MAX auto-approve lifecycle state."""
from alembic import op
import sqlalchemy as sa

revision = "0025_auto_approve_lifecycle"
down_revision = "0024_traffic_engine_v1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "auto_approve_enabled" not in {item["name"] for item in sa.inspect(bind).get_columns("channels")}: 
        op.add_column("channels", sa.Column("auto_approve_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    if not sa.inspect(bind).has_table("max_pending_join_requests"):
        op.create_table(
        "max_pending_join_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_user_id", sa.String(128), nullable=False),
        sa.Column("remote_request_id", sa.String(255)),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approve_sent_at", sa.DateTime(timezone=True)),
        sa.Column("user_added_at", sa.DateTime(timezone=True)),
        sa.Column("approve_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel_id", "max_user_id"),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("max_pending_join_requests")}
    for column in ("channel_id", "max_user_id", "status", "first_seen_at"):
        name = "ix_max_pending_join_requests_" + column
        if name not in indexes:
            op.create_index(name, "max_pending_join_requests", [column])


def downgrade():
    op.drop_table("max_pending_join_requests")
    op.drop_column("channels", "auto_approve_enabled")
