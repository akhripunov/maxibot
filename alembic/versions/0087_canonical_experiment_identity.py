"""Add canonical per-object experiment identity to existing ADS records."""
from alembic import op
import sqlalchemy as sa

revision = "0087_canonical_experiment_identity"
down_revision = "0086_ads_autopilot_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads_recommendations", sa.Column("experiment_id", sa.String(length=36), nullable=True))
    op.add_column("ads_recommendations", sa.Column("experiment_seq", sa.Integer(), nullable=True))
    op.add_column("ads_recommendations", sa.Column("experiment_status", sa.String(length=24), nullable=True, server_default="planned"))
    op.add_column("ads_ai_outcomes", sa.Column("experiment_id", sa.String(length=36), nullable=True))
    op.add_column("ads_memories", sa.Column("experiment_id", sa.String(length=36), nullable=True))
    op.add_column("ads_memory_evidence", sa.Column("experiment_id", sa.String(length=36), nullable=True))
    op.execute("UPDATE ads_recommendations SET experiment_id = id WHERE experiment_id IS NULL")
    op.execute("UPDATE ads_recommendations SET experiment_status = 'planned' WHERE experiment_status IS NULL")
    op.execute("UPDATE ads_ai_outcomes o SET experiment_id = r.experiment_id FROM ads_recommendations r WHERE o.recommendation_id = r.id AND o.experiment_id IS NULL")
    op.alter_column("ads_recommendations", "experiment_status", nullable=False, server_default="planned")
    for table in ("ads_recommendations", "ads_ai_outcomes", "ads_memories", "ads_memory_evidence"):
        op.create_index(f"ix_{table}_experiment_id", table, ["experiment_id"])


def downgrade() -> None:
    for table in ("ads_memory_evidence", "ads_memories", "ads_ai_outcomes", "ads_recommendations"):
        op.drop_index(f"ix_{table}_experiment_id", table_name=table)
    op.drop_column("ads_memory_evidence", "experiment_id")
    op.drop_column("ads_memories", "experiment_id")
    op.drop_column("ads_ai_outcomes", "experiment_id")
    op.drop_column("ads_recommendations", "experiment_status")
    op.drop_column("ads_recommendations", "experiment_seq")
    op.drop_column("ads_recommendations", "experiment_id")
