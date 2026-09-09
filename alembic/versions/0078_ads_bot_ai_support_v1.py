"""Allow the existing ADS AI records to be scoped to a BOT promotion object.

Revision ID: 0078_ads_bot_ai_support_v1
Revises: 0077_ads_storage_lifecycle_v1
"""
from alembic import op
import sqlalchemy as sa


revision = "0078_ads_bot_ai_support_v1"
down_revision = "0077_ads_storage_lifecycle_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("ads_analysis_snapshots", "ads_ai_runs", "ads_memories", "ads_data_health"):
        op.add_column(table, sa.Column("promotion_object_type", sa.String(length=16), nullable=True))
        op.add_column(table, sa.Column("promotion_object_id", sa.String(length=36), nullable=True))
        op.create_index(f"ix_{table}_promotion_object_type", table, ["promotion_object_type"])
        op.create_index(f"ix_{table}_promotion_object_id", table, ["promotion_object_id"])
    op.alter_column("ads_ai_runs", "channel_id", existing_type=sa.String(length=36), nullable=True)
    op.alter_column("ads_recommendations", "channel_id", existing_type=sa.String(length=36), nullable=True)
    op.alter_column("ads_data_health", "channel_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    op.alter_column("ads_data_health", "channel_id", existing_type=sa.String(length=36), nullable=False)
    op.alter_column("ads_recommendations", "channel_id", existing_type=sa.String(length=36), nullable=False)
    op.alter_column("ads_ai_runs", "channel_id", existing_type=sa.String(length=36), nullable=False)
    for table in ("ads_data_health", "ads_memories", "ads_ai_runs", "ads_analysis_snapshots"):
        op.drop_index(f"ix_{table}_promotion_object_id", table_name=table)
        op.drop_index(f"ix_{table}_promotion_object_type", table_name=table)
        op.drop_column(table, "promotion_object_id")
        op.drop_column(table, "promotion_object_type")
