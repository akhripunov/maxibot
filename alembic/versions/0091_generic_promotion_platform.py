"""Add destination-platform identity to existing ADS scopes."""

from alembic import op
import sqlalchemy as sa

revision = "0091_generic_promotion_platform"
down_revision = "0090_self_learning_loop_v2"
branch_labels = None
depends_on = None

_TABLES = (
    "ads_promotion_campaign_links", "ads_collection_runs", "ads_analysis_snapshots",
    "ads_ai_runs", "ads_recommendations", "ads_memories", "ads_data_health",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("destination_platform", sa.String(length=24), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET destination_platform = 'MAX' WHERE destination_platform IS NULL"))
        op.create_index(f"ix_{table}_destination_platform", table, ["destination_platform"])


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_destination_platform", table_name=table)
        op.drop_column(table, "destination_platform")

