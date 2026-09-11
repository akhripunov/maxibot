"""Track current MAX bot recipient block state for owner diagnostics."""

from alembic import op
import sqlalchemy as sa

revision = "0085_bot_recipient_state_v1"
down_revision = "0084_offline_conversion_goal_unique_v1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_recipient_states",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("bot_key", sa.String(length=128), nullable=False),
        sa.Column("user_external_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bot_key", "user_external_id", name="uq_bot_recipient_state_bot_user"),
    )
    op.create_index("ix_bot_recipient_states_bot_key", "bot_recipient_states", ["bot_key"])
    op.create_index("ix_bot_recipient_states_user_external_id", "bot_recipient_states", ["user_external_id"])
    op.create_index("ix_bot_recipient_states_status", "bot_recipient_states", ["status"])
    op.create_index("ix_bot_recipient_states_blocked_at", "bot_recipient_states", ["blocked_at"])


def downgrade():
    op.drop_index("ix_bot_recipient_states_blocked_at", table_name="bot_recipient_states")
    op.drop_index("ix_bot_recipient_states_status", table_name="bot_recipient_states")
    op.drop_index("ix_bot_recipient_states_user_external_id", table_name="bot_recipient_states")
    op.drop_index("ix_bot_recipient_states_bot_key", table_name="bot_recipient_states")
    op.drop_table("bot_recipient_states")
