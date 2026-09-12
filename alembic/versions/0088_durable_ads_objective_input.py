"""Persist the AI objective input context across worker restarts."""
from alembic import op
import sqlalchemy as sa

revision = "0088_durable_ads_objective_input"
down_revision = "0087_canonical_experiment_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admins", sa.Column("pending_ads_object_type", sa.String(length=16), nullable=True))
    op.add_column("admins", sa.Column("pending_ads_object_id", sa.String(length=128), nullable=True))
    op.add_column("admins", sa.Column("pending_ads_campaign_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("admins", "pending_ads_campaign_id")
    op.drop_column("admins", "pending_ads_object_id")
    op.drop_column("admins", "pending_ads_object_type")
