"""Extend the existing creative catalog with bounded storage metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0080_creative_storage_intelligence_v1"
down_revision = "0079_ads_objective_target_cpa_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads_creatives", sa.Column("source_type", sa.String(length=32), nullable=True))
    op.add_column("ads_creatives", sa.Column("storage_provider", sa.String(length=32), nullable=True))
    op.add_column("ads_creatives", sa.Column("storage_key", sa.String(length=1024), nullable=True))
    op.add_column("ads_creatives", sa.Column("media_storage_state", sa.String(length=32), nullable=True))
    op.add_column("ads_creatives", sa.Column("mime_type", sa.String(length=128), nullable=True))
    op.add_column("ads_creatives", sa.Column("file_size", sa.BigInteger(), nullable=True))
    op.add_column("ads_creatives", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("ads_creatives", sa.Column("height", sa.Integer(), nullable=True))
    op.add_column("ads_creatives", sa.Column("duration_seconds", sa.Numeric(20, 6), nullable=True))
    op.add_column("ads_creatives", sa.Column("ai_features_json", sa.Text(), nullable=True))
    op.add_column("ads_creatives", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ads_creatives", sa.Column("provider_state", sa.String(length=64), nullable=True))
    op.create_index("ix_ads_creatives_source_type", "ads_creatives", ["source_type"])
    op.create_index("ix_ads_creatives_storage_key", "ads_creatives", ["storage_key"])
    op.create_index("ix_ads_creatives_media_storage_state", "ads_creatives", ["media_storage_state"])
    op.create_index("ix_ads_creatives_last_seen_at", "ads_creatives", ["last_seen_at"])
    op.execute("UPDATE ads_creatives SET ai_features_json = '{}' WHERE ai_features_json IS NULL")


def downgrade() -> None:
    for name in ("ix_ads_creatives_last_seen_at", "ix_ads_creatives_media_storage_state", "ix_ads_creatives_storage_key", "ix_ads_creatives_source_type"):
        op.drop_index(name, table_name="ads_creatives")
    for name in ("provider_state", "last_seen_at", "ai_features_json", "duration_seconds", "height", "width", "file_size", "mime_type", "media_storage_state", "storage_key", "storage_provider", "source_type"):
        op.drop_column("ads_creatives", name)
