"""Persist service tier and isolated AI promotion entitlement."""

from alembic import op
import sqlalchemy as sa

revision = "0083_subscription_entitlements_v1"
down_revision = "0082_subscription_tariffs_v1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admins", sa.Column("subscription_tariff", sa.String(length=16), nullable=False, server_default="free"))
    op.add_column("admins", sa.Column("ai_promotion_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscription_orders", sa.Column("order_kind", sa.String(length=24), nullable=False, server_default="service"))


def downgrade():
    op.drop_column("subscription_orders", "order_kind")
    op.drop_column("admins", "ai_promotion_expires_at")
    op.drop_column("admins", "subscription_tariff")
