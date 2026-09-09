"""Add non-destructive lease metadata for temporary Codex worker runs."""
from alembic import op
import sqlalchemy as sa
revision="0049_ads_codex_worker_lease"
down_revision="0048_ads_shadow_test_marker"
branch_labels=None
depends_on=None
def upgrade():
    for column in (sa.Column("worker_id",sa.String(128)),sa.Column("claimed_at",sa.DateTime(timezone=True)),sa.Column("lease_expires_at",sa.DateTime(timezone=True)),sa.Column("lease_id",sa.String(64)),sa.Column("retry_count",sa.Integer(),nullable=False,server_default="0")):
        op.add_column("ads_ai_runs",column)
    op.create_index("ix_ads_ai_runs_lease_expires_at","ads_ai_runs",["lease_expires_at"])
    op.create_index("ix_ads_ai_runs_lease_id","ads_ai_runs",["lease_id"],unique=True)
    op.alter_column("ads_ai_runs","retry_count",server_default=None)
def downgrade():
    op.drop_index("ix_ads_ai_runs_lease_id",table_name="ads_ai_runs");op.drop_index("ix_ads_ai_runs_lease_expires_at",table_name="ads_ai_runs")
    for name in ("retry_count","lease_id","lease_expires_at","claimed_at","worker_id"):op.drop_column("ads_ai_runs",name)
