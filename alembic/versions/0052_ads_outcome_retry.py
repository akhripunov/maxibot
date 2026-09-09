"""Add bounded retry scheduling for V2 outcome collection."""
from alembic import op
import sqlalchemy as sa

revision = "0052_ads_outcome_retry"
down_revision = "0051_ads_data_layer_v2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ads_v2_outcomes", sa.Column("next_retry_at", sa.DateTime(timezone=True)))
    op.create_index("ix_ads_v2_outcomes_next_retry_at", "ads_v2_outcomes", ["next_retry_at"])


def downgrade():
    op.drop_index("ix_ads_v2_outcomes_next_retry_at", table_name="ads_v2_outcomes")
    op.drop_column("ads_v2_outcomes", "next_retry_at")
