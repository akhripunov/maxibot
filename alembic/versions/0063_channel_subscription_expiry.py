"""Store the paid subscription expiry for each MAXIBOT user."""

from alembic import op
import sqlalchemy as sa


revision = "0063_channel_subscription_expiry"
down_revision = "0062_subscription_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admins", sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_admins_subscription_expires_at", "admins", ["subscription_expires_at"])
    # Preserve already confirmed payments when the entitlement column is first
    # introduced. Future payments are activated by the Robokassa callback.
    op.execute(
        sa.text(
            """
            UPDATE admins AS a
            SET subscription_expires_at = paid.expiry
            FROM (
                SELECT o.admin_id,
                       MAX(o.paid_at + (o.months || ' months')::interval) AS expiry
                FROM subscription_orders AS o
                WHERE o.status = 'paid' AND o.paid_at IS NOT NULL
                GROUP BY o.admin_id
            ) AS paid
            WHERE a.id = paid.admin_id AND a.subscription_expires_at IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_admins_subscription_expires_at", table_name="admins")
    op.drop_column("admins", "subscription_expires_at")
