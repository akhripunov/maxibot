"""Persist an independent per-experiment budget cap on promotion links.

The cap is nullable for historical links so a deployment cannot silently
authorize autonomous spending.  The Control Plane fails closed until an
explicit cap is configured for the exact promotion/campaign scope.
"""
from alembic import op
import sqlalchemy as sa

revision = "0097_experiment_budget_cap_v1"
down_revision = "0096_experience_transfer_prior_engine_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads_promotion_campaign_links", sa.Column("experiment_budget_cap", sa.Numeric(20, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("ads_promotion_campaign_links", "experiment_budget_cap")
