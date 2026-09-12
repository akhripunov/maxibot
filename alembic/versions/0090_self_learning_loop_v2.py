"""Persist maturity, learning trace, reservations and guardrail snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0090_self_learning_loop_v2"
down_revision = "0089_creative_experiment_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = [
        ("hypothesis_key", sa.String(128), True),
        ("maturity_before", sa.String(16), True),
        ("maturity_after", sa.String(16), True),
        ("maturity_reason", sa.Text(), True),
        ("influenced_by_memory_ids_json", sa.Text(), False, "[]"),
        ("avoided_due_to_memory_ids_json", sa.Text(), False, "[]"),
        ("experiment_budget_cap", sa.Numeric(20, 6), True),
        ("reserved_budget", sa.Numeric(20, 6), False, "0"),
        ("reservation_status", sa.String(16), False, "NONE"),
        ("guardrail_decision_json", sa.Text(), True),
    ]
    for item in columns:
        name, typ, nullable, *default = item
        kwargs = {"nullable": nullable}
        if default:
            kwargs["server_default"] = default[0]
        op.add_column("ads_recommendations", sa.Column(name, typ, **kwargs))
    for name in ("hypothesis_key", "reservation_status"):
        op.create_index(f"ix_ads_recommendations_{name}", "ads_recommendations", [name])


def downgrade() -> None:
    for name in ("hypothesis_key", "reservation_status"):
        op.drop_index(f"ix_ads_recommendations_{name}", table_name="ads_recommendations")
    for name in ("guardrail_decision_json", "reservation_status", "reserved_budget", "experiment_budget_cap", "avoided_due_to_memory_ids_json", "influenced_by_memory_ids_json", "maturity_reason", "maturity_after", "maturity_before", "hypothesis_key"):
        op.drop_column("ads_recommendations", name)
