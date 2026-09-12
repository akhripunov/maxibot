"""Canonical creative experiment control/treatment state.

Extends existing experiment and attribution records; deliberately creates no
new ADS table.
"""
from alembic import op
import sqlalchemy as sa

revision = "0089_creative_experiment_v1"
down_revision = "0088_durable_ads_objective_input"
branch_labels = None
depends_on = None


def _columns(table: str) -> list[tuple[str, sa.Column]]:
    return [
        ("experiment_id", sa.Column("experiment_id", sa.String(length=36), nullable=True)),
        ("experiment_variant", sa.Column("experiment_variant", sa.String(length=16), nullable=True)),
        ("provider_ad_id", sa.Column("provider_ad_id", sa.String(length=128), nullable=True)),
        ("provider_creative_id", sa.Column("provider_creative_id", sa.String(length=128), nullable=True)),
    ]


def upgrade() -> None:
    for table in ("traffic_visits", "bot_traffic_visits", "system_traffic_visits"):
        for name, column in _columns(table):
            op.add_column(table, column)
            op.create_index(f"ix_{table}_{name}", table, [name])

    op.add_column("ads_recommendations", sa.Column("experiment_kind", sa.String(length=32), nullable=True))
    op.add_column("ads_recommendations", sa.Column("control_ad_entity_id", sa.String(length=36), nullable=True))
    op.add_column("ads_recommendations", sa.Column("control_creative_id", sa.String(length=36), nullable=True))
    op.add_column("ads_recommendations", sa.Column("treatment_ad_entity_id", sa.String(length=36), nullable=True))
    op.add_column("ads_recommendations", sa.Column("treatment_creative_id", sa.String(length=36), nullable=True))
    op.add_column("ads_recommendations", sa.Column("creative_selection_reason", sa.Text(), nullable=True))
    op.add_column("ads_recommendations", sa.Column("provider_lifecycle", sa.String(length=32), nullable=False, server_default="PLANNED"))
    op.add_column("ads_recommendations", sa.Column("measurement_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ads_recommendations", sa.Column("control_variant_token", sa.String(length=64), nullable=True))
    op.add_column("ads_recommendations", sa.Column("treatment_variant_token", sa.String(length=64), nullable=True))
    op.create_index("ix_ads_recommendations_experiment_kind", "ads_recommendations", ["experiment_kind"])
    op.create_index("ix_ads_recommendations_control_ad_entity_id", "ads_recommendations", ["control_ad_entity_id"])
    op.create_index("ix_ads_recommendations_control_creative_id", "ads_recommendations", ["control_creative_id"])
    op.create_index("ix_ads_recommendations_treatment_ad_entity_id", "ads_recommendations", ["treatment_ad_entity_id"])
    op.create_index("ix_ads_recommendations_treatment_creative_id", "ads_recommendations", ["treatment_creative_id"])
    op.create_index("ix_ads_recommendations_provider_lifecycle", "ads_recommendations", ["provider_lifecycle"])
    op.create_index("ix_ads_recommendations_measurement_started_at", "ads_recommendations", ["measurement_started_at"])
    op.create_index("ix_ads_recommendations_control_variant_token", "ads_recommendations", ["control_variant_token"])
    op.create_index("ix_ads_recommendations_treatment_variant_token", "ads_recommendations", ["treatment_variant_token"])
    op.create_foreign_key("fk_ads_recommendations_control_ad_entity", "ads_recommendations", "ads_entities", ["control_ad_entity_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_ads_recommendations_control_creative", "ads_recommendations", "ads_creatives", ["control_creative_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_ads_recommendations_treatment_ad_entity", "ads_recommendations", "ads_entities", ["treatment_ad_entity_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_ads_recommendations_treatment_creative", "ads_recommendations", "ads_creatives", ["treatment_creative_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    for name in ("fk_ads_recommendations_treatment_creative", "fk_ads_recommendations_treatment_ad_entity", "fk_ads_recommendations_control_creative", "fk_ads_recommendations_control_ad_entity"):
        op.drop_constraint(name, "ads_recommendations", type_="foreignkey")
    for name in ("treatment_variant_token", "control_variant_token", "measurement_started_at", "provider_lifecycle", "treatment_creative_id", "treatment_ad_entity_id", "control_creative_id", "control_ad_entity_id", "experiment_kind"):
        op.drop_index(f"ix_ads_recommendations_{name}", table_name="ads_recommendations")
    for name in ("treatment_variant_token", "control_variant_token", "measurement_started_at", "provider_lifecycle", "creative_selection_reason", "treatment_creative_id", "treatment_ad_entity_id", "control_creative_id", "control_ad_entity_id", "experiment_kind"):
        op.drop_column("ads_recommendations", name)
    for table in ("traffic_visits", "bot_traffic_visits", "system_traffic_visits"):
        for name, _ in reversed(_columns(table)):
            op.drop_index(f"ix_{table}_{name}", table_name=table)
            op.drop_column(table, name)
