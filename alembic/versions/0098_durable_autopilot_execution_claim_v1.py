"""Add durable idempotent claims for Autopilot provider mutations."""
from alembic import op
import sqlalchemy as sa

revision = "0098_durable_autopilot_execution_claim_v1"
down_revision = "0097_experiment_budget_cap_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads_ai_outcomes", sa.Column("execution_key", sa.String(length=64), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("execution_state", sa.String(length=32), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("execution_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("execution_scope_type", sa.String(length=16), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("execution_scope_id", sa.String(length=36), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("execution_campaign_id", sa.String(length=36), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("execution_action_type", sa.String(length=64), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("rollback_state_json", sa.Text(), nullable=True))
    op.add_column("ads_ai_outcomes", sa.Column("prepared_request_json", sa.Text(), nullable=True))
    op.create_index("ix_ads_ai_outcomes_execution_key", "ads_ai_outcomes", ["execution_key"], unique=True)
    op.create_index("ix_ads_ai_outcomes_execution_state", "ads_ai_outcomes", ["execution_state"])
    op.create_index("ix_ads_ai_outcomes_execution_lease_expires_at", "ads_ai_outcomes", ["execution_lease_expires_at"])
    op.create_index("ix_ads_ai_outcomes_execution_campaign_id", "ads_ai_outcomes", ["execution_campaign_id"])
    op.create_index(
        "uq_ads_ai_outcome_active_execution_scope", "ads_ai_outcomes",
        ["execution_scope_type", "execution_scope_id", "execution_campaign_id"], unique=True,
        postgresql_where=sa.text("execution_state IN ('CLAIMED','APPLYING','NEEDS_RECONCILIATION','IMPLEMENTED')"),
        sqlite_where=sa.text("execution_state IN ('CLAIMED','APPLYING','NEEDS_RECONCILIATION','IMPLEMENTED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_ads_ai_outcome_active_execution_scope", table_name="ads_ai_outcomes")
    op.drop_index("ix_ads_ai_outcomes_execution_campaign_id", table_name="ads_ai_outcomes")
    op.drop_index("ix_ads_ai_outcomes_execution_lease_expires_at", table_name="ads_ai_outcomes")
    op.drop_index("ix_ads_ai_outcomes_execution_state", table_name="ads_ai_outcomes")
    op.drop_index("ix_ads_ai_outcomes_execution_key", table_name="ads_ai_outcomes")
    for column in (
        "prepared_request_json", "rollback_state_json", "execution_action_type",
        "execution_campaign_id", "execution_scope_id", "execution_scope_type",
        "execution_lease_expires_at", "execution_claimed_at", "execution_state", "execution_key",
    ):
        op.drop_column("ads_ai_outcomes", column)
