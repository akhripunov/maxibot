"""Add versioned context/prior metadata to existing ADS memory tables.

No new table is introduced. Historical values remain nullable/unknown and are
never promoted into cross-tenant knowledge by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0096_experience_transfer_prior_engine_v1"
down_revision = "0095_ads_account_regional_ownership_v1"
branch_labels = None
depends_on = None

_COLUMNS = {
    "ads_analysis_snapshots": [
        ("context_signature_json", sa.Text()), ("context_signature_hash", sa.String(64)),
        ("context_signature_version", sa.String(32)),
    ],
    "ads_recommendations": [
        ("context_signature_json", sa.Text()), ("context_signature_hash", sa.String(64)),
        ("prior_scores_json", sa.Text()), ("prior_artifact_version", sa.String(64)),
    ],
    "ads_memories": [
        ("context_signature_json", sa.Text()), ("context_signature_hash", sa.String(64)),
        ("causal_strength", sa.String(16)), ("source_count", sa.Integer(), 0),
        ("independent_context_count", sa.Integer(), 0), ("success_count", sa.Integer(), 0),
        ("failure_count", sa.Integer(), 0), ("inconclusive_count", sa.Integer(), 0),
        ("effect_direction", sa.String(16)), ("effect_size", sa.Numeric(20, 8)),
        ("applicability_json", sa.Text()), ("prior_status", sa.String(24)),
        ("prior_artifact_version", sa.String(64)),
    ],
    "ads_memory_evidence": [
        ("context_signature_json", sa.Text()), ("context_signature_hash", sa.String(64)),
        ("causal_strength", sa.String(16)),
    ],
}

def upgrade() -> None:
    for table, columns in _COLUMNS.items():
        for spec in columns:
            name, type_ = spec[0], spec[1]
            kwargs = {"nullable": True}
            if len(spec) == 3:
                kwargs.update(server_default=str(spec[2]), nullable=False)
            op.add_column(table, sa.Column(name, type_, **kwargs))
            if name.endswith("_hash"):
                op.create_index(f"ix_{table}_{name}", table, [name])

def downgrade() -> None:
    for table, columns in reversed(list(_COLUMNS.items())):
        for spec in reversed(columns):
            name = spec[0]
            if name.endswith("_hash"):
                op.drop_index(f"ix_{table}_{name}", table_name=table)
            op.drop_column(table, name)
