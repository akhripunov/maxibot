"""Add granular observation provenance and retry identity."""
from alembic import op
import sqlalchemy as sa

revision = "0055_granular_obs_identity"
down_revision = "0054_ads_storage_governance"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ads_v2_observations", sa.Column("collection_run_id", sa.String(36), sa.ForeignKey("ads_v2_collection_runs.id", ondelete="SET NULL")))
    op.add_column("ads_v2_observations", sa.Column("logical_key", sa.String(64), nullable=True))
    op.create_index("ix_ads_v2_observations_collection_run_id", "ads_v2_observations", ["collection_run_id"])
    # Existing rows are historical campaign observations. Their snapshot and
    # row identity are sufficient to backfill a stable, non-colliding key.
    op.execute("UPDATE ads_v2_observations SET logical_key = md5(id) WHERE logical_key IS NULL")
    op.alter_column("ads_v2_observations", "logical_key", nullable=False)
    op.create_index("ix_ads_v2_observations_logical_key", "ads_v2_observations", ["logical_key"], unique=True)


def downgrade():
    op.drop_index("ix_ads_v2_observations_logical_key", table_name="ads_v2_observations")
    op.drop_index("ix_ads_v2_observations_collection_run_id", table_name="ads_v2_observations")
    op.drop_column("ads_v2_observations", "logical_key")
    op.drop_column("ads_v2_observations", "collection_run_id")
