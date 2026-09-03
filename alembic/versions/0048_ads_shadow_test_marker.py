"""Add a non-production marker for development/manual ADS runs."""
from alembic import op
import sqlalchemy as sa

revision = "0048_ads_shadow_test_marker"
down_revision = "0047_ads_v1_completion"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ads_ai_runs", sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("ads_ai_runs", "is_test", server_default=None)


def downgrade():
    op.drop_column("ads_ai_runs", "is_test")
