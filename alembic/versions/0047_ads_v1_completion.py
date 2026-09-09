"""Complete ADS V1 audit, memory provenance and AI-run fields."""
from alembic import op
import sqlalchemy as sa

revision = "0047_ads_v1_completion"
down_revision = "0046_ads_v1_foundation"
branch_labels = None
depends_on = None


def upgrade():
    for name, column in (
        ("campaign_id", sa.Column("campaign_id", sa.String(36), sa.ForeignKey("ads_campaigns.id", ondelete="SET NULL"))),
        ("prompt_version", sa.Column("prompt_version", sa.String(64))),
        ("schema_version", sa.Column("schema_version", sa.String(64))),
        ("safe_context_json", sa.Column("safe_context_json", sa.Text())),
        ("raw_structured_output_json", sa.Column("raw_structured_output_json", sa.Text())),
        ("validated_output_json", sa.Column("validated_output_json", sa.Text())),
        ("validation_error", sa.Column("validation_error", sa.Text())),
        ("policy_result_json", sa.Column("policy_result_json", sa.Text())),
        ("completed_at", sa.Column("completed_at", sa.DateTime(timezone=True))),
    ):
        op.add_column("ads_ai_runs", column)
    op.create_index("ix_ads_ai_runs_campaign_id", "ads_ai_runs", ["campaign_id"])
    for column in (
        sa.Column("action_code", sa.String(64), nullable=False, server_default="INSUFFICIENT_DATA"),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="LOW"),
        sa.Column("data_sufficiency", sa.String(24), nullable=False, server_default="INSUFFICIENT"),
        sa.Column("policy_json", sa.Text()),
    ):
        op.add_column("ads_recommendations", column)
    for column in (
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("ads_campaigns.id", ondelete="SET NULL")),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="AI_INFERENCE"),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="LOW"),
        sa.Column("superseded_by", sa.String(36), sa.ForeignKey("ads_memories.id", ondelete="SET NULL")),
    ):
        op.add_column("ads_memories", column)
    op.create_index("ix_ads_memories_campaign_id", "ads_memories", ["campaign_id"])
    op.create_table("ads_memory_evidence", sa.Column("id", sa.String(36), primary_key=True), sa.Column("memory_id", sa.String(36), sa.ForeignKey("ads_memories.id", ondelete="CASCADE"), nullable=False), sa.Column("evidence_type", sa.String(32), nullable=False), sa.Column("evidence_ref_id", sa.String(128), nullable=False), sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("ads_snapshots.id", ondelete="SET NULL")), sa.Column("ai_run_id", sa.String(36), sa.ForeignKey("ads_ai_runs.id", ondelete="SET NULL")), sa.Column("description", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_ads_memory_evidence_memory_id", "ads_memory_evidence", ["memory_id"])
    op.create_index("ix_ads_memory_evidence_evidence_ref_id", "ads_memory_evidence", ["evidence_ref_id"])


def downgrade():
    op.drop_table("ads_memory_evidence")
    op.drop_index("ix_ads_memories_campaign_id", table_name="ads_memories")
    for column in ("superseded_by", "confidence", "source_type", "campaign_id"):
        op.drop_column("ads_memories", column)
    for column in ("policy_json", "data_sufficiency", "confidence", "action_code"):
        op.drop_column("ads_recommendations", column)
    op.drop_index("ix_ads_ai_runs_campaign_id", table_name="ads_ai_runs")
    for column in ("completed_at", "policy_result_json", "validation_error", "validated_output_json", "raw_structured_output_json", "safe_context_json", "schema_version", "prompt_version", "campaign_id"):
        op.drop_column("ads_ai_runs", column)
