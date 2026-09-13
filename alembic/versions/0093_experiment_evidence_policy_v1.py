"""Persist deterministic evidence policy on canonical experiments."""
from alembic import op
import sqlalchemy as sa

revision = "0093_experiment_evidence_policy_v1"
down_revision = "0092_split_yandex_oauth_credentials"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("ads_recommendations", sa.Column("evidence_policy_json", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("ads_recommendations", "evidence_policy_json")
