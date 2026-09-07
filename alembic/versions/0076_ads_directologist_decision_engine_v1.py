"""Add bounded decision-engine fields to existing ADS records.

Revision ID: 0076_ads_directologist_decision_engine_v1
Revises: 0075_ads_canonical_v2
"""
from alembic import op
import sqlalchemy as sa


revision = "0076_ads_directologist_decision_engine_v1"
down_revision = "0075_ads_canonical_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads_recommendations", sa.Column("problem_json", sa.Text(), nullable=True))
    op.add_column("ads_recommendations", sa.Column("root_cause_json", sa.Text(), nullable=True))
    op.add_column("ads_recommendations", sa.Column("hypothesis", sa.Text(), nullable=True))
    op.add_column("ads_recommendations", sa.Column("expected_impact_json", sa.Text(), nullable=True))
    op.add_column("ads_recommendations", sa.Column("evaluation_window_days", sa.Integer(), nullable=True))
    op.add_column("ads_recommendations", sa.Column("evaluation_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ads_recommendations", sa.Column("promotion_object_type", sa.String(length=16), nullable=True))
    op.add_column("ads_recommendations", sa.Column("promotion_object_id", sa.String(length=36), nullable=True))

    op.add_column("ads_ai_outcomes", sa.Column("evaluation_status", sa.String(length=24), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("implementation_confirmed", sa.String(length=16), nullable=False, server_default="UNKNOWN"))
    op.add_column("ads_ai_outcomes", sa.Column("implementation_evidence_json", sa.Text(), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("baseline_metrics_json", sa.Text(), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("expected_direction", sa.String(length=16), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("expected_impact_json", sa.Text(), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("actual_metrics_json", sa.Text(), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("confidence", sa.String(length=16), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("reason_json", sa.Text(), nullable=True))
    op.alter_column("ads_ai_outcomes", "implementation_confirmed", server_default=None)


def downgrade() -> None:
    for column in (
        "reason_json", "confidence", "actual_metrics_json", "expected_impact_json",
        "expected_direction", "baseline_metrics_json", "implementation_evidence_json",
        "implementation_confirmed", "evaluation_status",
    ):
        op.drop_column("ads_ai_outcomes", column)
    for column in (
        "promotion_object_id", "promotion_object_type", "evaluation_at",
        "evaluation_window_days", "expected_impact_json", "hypothesis",
        "root_cause_json", "problem_json",
    ):
        op.drop_column("ads_recommendations", column)
