"""Add provider-independent ADS V2 entities, observations and outcomes."""
from alembic import op
import sqlalchemy as sa

revision = "0051_ads_data_layer_v2"
down_revision = "0050_ads_ai_ui_origin"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ads_v2_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("provider_entity_id", sa.String(128), nullable=False),
        sa.Column("parent_id", sa.String(36), sa.ForeignKey("ads_v2_entities.id", ondelete="SET NULL")),
        sa.Column("display_name", sa.String(512)), sa.Column("status", sa.String(64)),
        sa.Column("provider_metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "entity_type", "provider_entity_id", name="uq_ads_v2_entity_identity"),
    )
    for name, cols in (("provider", ["provider"]), ("entity_type", ["entity_type"]), ("provider_entity_id", ["provider_entity_id"]), ("parent", ["parent_id"]),):
        op.create_index(f"ix_ads_v2_entities_{name}", "ads_v2_entities", cols)
    op.create_table(
        "ads_v2_report_runs",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("report_type", sa.String(128), nullable=False), sa.Column("campaign_provider_id", sa.String(128)),
        sa.Column("date_from", sa.Date()), sa.Column("date_to", sa.Date()), sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("requested_fields_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("request_hash", sa.String(64)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False), sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parser_version", sa.String(64), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("error_code", sa.String(96)), sa.Column("freshness_caveat", sa.Text()),
    )
    for name, cols in (("provider", ["provider"]), ("campaign", ["campaign_provider_id"]), ("request_hash", ["request_hash"]), ("status", ["status"])):
        op.create_index(f"ix_ads_v2_report_runs_{name}", "ads_v2_report_runs", cols)
    op.create_table(
        "ads_v2_observations",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("ads_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("ads_v2_entities.id", ondelete="SET NULL")), sa.Column("dimension_type", sa.String(32), nullable=False), sa.Column("dimension_value", sa.String(256)),
        sa.Column("window", sa.String(8), nullable=False), sa.Column("date_from", sa.Date()), sa.Column("date_to", sa.Date()),
        sa.Column("impressions", sa.Numeric(20, 6)), sa.Column("clicks", sa.Numeric(20, 6)), sa.Column("cost", sa.Numeric(20, 6)), sa.Column("conversions", sa.Numeric(20, 6)),
        sa.Column("fact_id", sa.String(32)), sa.Column("report_run_id", sa.String(36), sa.ForeignKey("ads_v2_report_runs.id", ondelete="SET NULL")), sa.Column("provenance_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, cols in (("snapshot", ["snapshot_id"]), ("entity", ["entity_id"]), ("dimension", ["dimension_type"]), ("window", ["window"]), ("fact", ["fact_id"]), ("report", ["report_run_id"])):
        op.create_index(f"ix_ads_v2_observations_{name}", "ads_v2_observations", cols)
    op.create_table(
        "ads_v2_outcomes",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("recommendation_id", sa.String(36), sa.ForeignKey("ads_recommendations.id", ondelete="CASCADE"), nullable=False), sa.Column("horizon", sa.String(8), nullable=False), sa.Column("due_at", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("execution_status", sa.String(32), nullable=False), sa.Column("baseline_snapshot_id", sa.String(36), sa.ForeignKey("ads_snapshots.id", ondelete="SET NULL")), sa.Column("followup_snapshot_id", sa.String(36), sa.ForeignKey("ads_snapshots.id", ondelete="SET NULL")), sa.Column("observed_delta_json", sa.Text()), sa.Column("error_code", sa.String(96)), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("recommendation_id", "horizon", name="uq_ads_v2_outcome_horizon"),
    )
    for name, cols in (("recommendation", ["recommendation_id"]), ("due", ["due_at"]), ("status", ["status"]), ("baseline", ["baseline_snapshot_id"])):
        op.create_index(f"ix_ads_v2_outcomes_{name}", "ads_v2_outcomes", cols)


def downgrade():
    for name in ("baseline", "status", "due", "recommendation"):
        op.drop_index(f"ix_ads_v2_outcomes_{name}", table_name="ads_v2_outcomes")
    op.drop_table("ads_v2_outcomes")
    for name in ("report", "fact", "window", "dimension", "entity", "snapshot"):
        op.drop_index(f"ix_ads_v2_observations_{name}", table_name="ads_v2_observations")
    op.drop_table("ads_v2_observations")
    for name in ("status", "request_hash", "campaign", "provider"):
        op.drop_index(f"ix_ads_v2_report_runs_{name}", table_name="ads_v2_report_runs")
    op.drop_table("ads_v2_report_runs")
    for name in ("parent", "provider_entity_id", "entity_type", "provider"):
        op.drop_index(f"ix_ads_v2_entities_{name}", table_name="ads_v2_entities")
    op.drop_table("ads_v2_entities")
