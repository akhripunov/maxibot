"""store safe AI worker diagnostics"""
from alembic import op
import sqlalchemy as sa

revision = "0058_ads_ai_worker_diag"
down_revision = "0057_join_intent_check"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("ads_ai_runs", sa.Column("worker_diagnostics_json", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("ads_ai_runs", "worker_diagnostics_json")
