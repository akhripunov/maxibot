"""Add only indexes required by bounded ADS retention scans.

Revision ID: 0077_ads_storage_lifecycle_v1
Revises: 0076_ads_directologist_decision_engine_v1
"""
from alembic import op
import sqlalchemy as sa


revision = "0077_ads_storage_lifecycle_v1"
down_revision = "0076_ads_directologist_decision_engine_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ads_observations", "snapshot_id", existing_type=sa.String(length=36), nullable=True)
    op.create_index("ix_ads_observations_date_to", "ads_observations", ["date_to"])
    op.create_index("ix_ads_collection_runs_completed_at", "ads_collection_runs", ["completed_at"])
    op.create_index("ix_ads_report_runs_fetched_at", "ads_report_runs", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_ads_report_runs_fetched_at", table_name="ads_report_runs")
    op.drop_index("ix_ads_collection_runs_completed_at", table_name="ads_collection_runs")
    op.drop_index("ix_ads_observations_date_to", table_name="ads_observations")
    op.alter_column("ads_observations", "snapshot_id", existing_type=sa.String(length=36), nullable=False)
