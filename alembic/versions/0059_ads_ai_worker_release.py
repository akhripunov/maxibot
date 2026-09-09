"""store the immutable release used to claim an AI run"""
from alembic import op
import sqlalchemy as sa

revision = "0059_ads_ai_worker_release"
down_revision = "0058_ads_ai_worker_diag"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("ads_ai_runs", sa.Column("worker_release_id", sa.String(length=128), nullable=True))

def downgrade() -> None:
    op.drop_column("ads_ai_runs", "worker_release_id")
