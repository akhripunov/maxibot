"""Persist the MAX UI origin for automatic AI result updates."""
from alembic import op
import sqlalchemy as sa

revision = "0050_ads_ai_ui_origin"
down_revision = "0049_ads_codex_worker_lease"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ads_ai_runs", sa.Column("ui_admin_external_id", sa.String(128), nullable=True))
    op.add_column("ads_ai_runs", sa.Column("ui_message_id", sa.String(128), nullable=True))
    op.create_index("ix_ads_ai_runs_ui_admin_external_id", "ads_ai_runs", ["ui_admin_external_id"])


def downgrade():
    op.drop_index("ix_ads_ai_runs_ui_admin_external_id", table_name="ads_ai_runs")
    op.drop_column("ads_ai_runs", "ui_message_id")
    op.drop_column("ads_ai_runs", "ui_admin_external_id")
