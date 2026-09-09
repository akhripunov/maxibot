"""Add BOT scope to existing ADS AI records on the production schema head."""
from alembic import op
import sqlalchemy as sa


revision = "0081_ads_bot_ai_support_v1"
down_revision = "0080_creative_storage_intelligence_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("ads_analysis_snapshots", "ads_ai_runs", "ads_memories", "ads_data_health"):
        columns = {column["name"] for column in inspector.get_columns(table)}
        for name in ("promotion_object_type", "promotion_object_id"):
            if name not in columns:
                op.add_column(table, sa.Column(name, sa.String(length=16 if name.endswith("type") else 36), nullable=True))
        indexes = {index["name"] for index in inspector.get_indexes(table)}
        for name in ("promotion_object_type", "promotion_object_id"):
            index_name = f"ix_{table}_{name}"
            if index_name not in indexes:
                op.create_index(index_name, table, [name])
    for table in ("ads_ai_runs", "ads_recommendations", "ads_data_health"):
        channel = next(column for column in inspector.get_columns(table) if column["name"] == "channel_id")
        if not channel["nullable"]:
            op.alter_column(table, "channel_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    # These fields are already part of the canonical 0080 schema through the
    # existing production migration chain.  Keep them intact on rollback so
    # existing 0080 data and schema are never removed.
    return None
