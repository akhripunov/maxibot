"""Add explicit destination type for shared traffic handoff.

Revision ID: 0066_shared_bot_handoff
Revises: 0065_qa_observability
"""
from alembic import op
import sqlalchemy as sa

revision = "0066_shared_bot_handoff"
down_revision = "0065_qa_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("traffic_visits", sa.Column("destination_type", sa.String(length=16), nullable=False, server_default="CHANNEL"))
    op.add_column("system_traffic_visits", sa.Column("destination_type", sa.String(length=16), nullable=False, server_default="BOT"))


def downgrade() -> None:
    op.drop_column("system_traffic_visits", "destination_type")
    op.drop_column("traffic_visits", "destination_type")
