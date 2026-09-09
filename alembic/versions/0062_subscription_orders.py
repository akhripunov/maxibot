"""Add Robokassa subscription orders."""

from alembic import op
import sqlalchemy as sa

revision = "0062_subscription_orders"
down_revision = "0061_daily_reports_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("admin_id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id"),
    )
    op.create_index("ix_subscription_orders_invoice_id", "subscription_orders", ["invoice_id"])
    op.create_index("ix_subscription_orders_admin_id", "subscription_orders", ["admin_id"])
    op.create_index("ix_subscription_orders_channel_id", "subscription_orders", ["channel_id"])
    op.create_index("ix_subscription_orders_status", "subscription_orders", ["status"])


def downgrade() -> None:
    op.drop_table("subscription_orders")
