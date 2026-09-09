"""Add forward-only paid BOT conversion idempotency."""
from alembic import op
import sqlalchemy as sa


revision = "0071_bot_paid_dedupe"
down_revision = "0070_service_subscription_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bot_conversion_queue", sa.Column("paid_journey_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_bot_conversion_paid_journey",
        "bot_conversion_queue",
        ["paid_journey_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_bot_conversion_paid_journey", "bot_conversion_queue", type_="unique")
    op.drop_column("bot_conversion_queue", "paid_journey_key")
