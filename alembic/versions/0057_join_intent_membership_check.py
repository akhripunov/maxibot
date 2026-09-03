"""Rotate bounded official MAX membership reconciliation."""

from alembic import op
import sqlalchemy as sa


revision = "0057_join_intent_check"
down_revision = "0056_max_polling_checkpoint"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("traffic_join_intents", sa.Column("membership_checked_at", sa.DateTime(timezone=True)))
    op.create_index("ix_traffic_join_intents_membership_checked_at", "traffic_join_intents", ["membership_checked_at"])


def downgrade():
    op.drop_index("ix_traffic_join_intents_membership_checked_at", table_name="traffic_join_intents")
    op.drop_column("traffic_join_intents", "membership_checked_at")
