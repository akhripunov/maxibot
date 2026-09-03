"""Add idempotent server-side read-only ADS collection runs."""
from alembic import op
import sqlalchemy as sa

revision = "0053_ads_periodic_collection"
down_revision = "0052_ads_outcome_retry"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ads_v2_collection_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection_type", sa.String(32), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_key", sa.String(192), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("ads_snapshots.id", ondelete="SET NULL")),
        sa.Column("error_code", sa.String(96)),
        sa.Column("freshness_caveat", sa.Text()),
    )
    for name, cols in (("provider", ["provider"]), ("account", ["account_id"]), ("campaign", ["campaign_id"]), ("scheduled", ["scheduled_at"]), ("status", ["status"]), ("retry", ["next_retry_at"]),):
        op.create_index(f"ix_ads_v2_collection_runs_{name}", "ads_v2_collection_runs", cols)


def downgrade():
    for name in ("retry", "status", "scheduled", "campaign", "account", "provider"):
        op.drop_index(f"ix_ads_v2_collection_runs_{name}", table_name="ads_v2_collection_runs")
    op.drop_table("ads_v2_collection_runs")
