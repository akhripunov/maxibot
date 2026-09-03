"""Add additive daily downsampling target for ADS V2."""
from alembic import op
import sqlalchemy as sa

revision = "0054_ads_storage_governance"
down_revision = "0053_ads_periodic_collection"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ads_v2_daily_aggregates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("ads_v2_entities.id", ondelete="SET NULL")),
        sa.Column("dimension_type", sa.String(32), nullable=False),
        sa.Column("dimension_value", sa.String(256)),
        sa.Column("aggregate_date", sa.Date(), nullable=False),
        sa.Column("impressions", sa.Numeric(20, 6)), sa.Column("clicks", sa.Numeric(20, 6)),
        sa.Column("cost", sa.Numeric(20, 6)), sa.Column("conversions", sa.Numeric(20, 6)),
        sa.Column("source_observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_from", sa.DateTime(timezone=True)), sa.Column("source_to", sa.DateTime(timezone=True)),
        sa.Column("aggregation_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("provenance_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "campaign_id", "entity_id", "dimension_type", "dimension_value", "aggregate_date", name="uq_ads_v2_daily_aggregate_identity"),
    )
    for name, cols in (("provider", ["provider"]), ("campaign", ["campaign_id"]), ("entity", ["entity_id"]), ("date", ["aggregate_date"]), ("created", ["created_at"])):
        op.create_index(f"ix_ads_v2_daily_aggregates_{name}", "ads_v2_daily_aggregates", cols)


def downgrade():
    for name in ("created", "date", "entity", "campaign", "provider"):
        op.drop_index(f"ix_ads_v2_daily_aggregates_{name}", table_name="ads_v2_daily_aggregates")
    op.drop_table("ads_v2_daily_aggregates")
