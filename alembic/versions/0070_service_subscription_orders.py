"""Allow service-wide subscription orders without a channel."""

from alembic import op


revision = "0070_service_subscription_orders"
down_revision = "0069_bot_delivery_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("subscription_orders", "channel_id", nullable=True)


def downgrade() -> None:
    op.alter_column("subscription_orders", "channel_id", nullable=False)
