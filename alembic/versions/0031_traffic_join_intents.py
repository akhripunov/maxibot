"""Add fail-closed verified channel handoff intents.

Revision ID: 0031_join_intents
Revises: 0030_attr_reason
"""
from alembic import op
import sqlalchemy as sa


revision = "0031_join_intents"
down_revision = "0030_attr_reason"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "traffic_join_intents" in tables:
        return
    op.create_table(
        "traffic_join_intents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("channel_id", sa.String(length=36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("visit_id", sa.String(length=36), sa.ForeignKey("traffic_visits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_user_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("visit_id", "channel_id", name="uq_traffic_join_intent_visit_channel"),
    )
    for name, columns in (
        ("ix_traffic_join_intents_channel_id", ["channel_id"]),
        ("ix_traffic_join_intents_visit_id", ["visit_id"]),
        ("ix_traffic_join_intents_max_user_id", ["max_user_id"]),
        ("ix_traffic_join_intents_status", ["status"]),
        ("ix_traffic_join_intents_expires_at", ["expires_at"]),
    ):
        op.create_index(name, "traffic_join_intents", columns)


def downgrade():
    op.drop_table("traffic_join_intents")
