"""Add durable delivery state to the separate BOT conversion queue."""

from alembic import op
import sqlalchemy as sa


revision = "0069_bot_delivery_state"
down_revision = "0068_generic_bot_traffic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_conversion_queue", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("bot_conversion_queue", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bot_conversion_queue", sa.Column("last_result", sa.String(length=32), nullable=True))
    op.add_column("bot_conversion_queue", sa.Column("last_error_class", sa.String(length=64), nullable=True))
    op.add_column("bot_conversion_queue", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_conversion_queue", "sent_at")
    op.drop_column("bot_conversion_queue", "last_error_class")
    op.drop_column("bot_conversion_queue", "last_result")
    op.drop_column("bot_conversion_queue", "last_attempt_at")
    op.drop_column("bot_conversion_queue", "attempt_count")
