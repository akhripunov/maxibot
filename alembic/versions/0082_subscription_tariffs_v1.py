"""Add service tariff marker to subscription orders.

Historical orders remain valid with a NULL tariff and continue to use the
existing Robokassa amount/entitlement flow.
"""

from alembic import op
import sqlalchemy as sa


revision = "0082_subscription_tariffs_v1"
down_revision = "0081_ads_bot_ai_support_v1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subscription_orders", sa.Column("tariff", sa.String(length=16), nullable=True))


def downgrade():
    op.drop_column("subscription_orders", "tariff")
