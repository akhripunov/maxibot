"""Add durable read-only AI promotion foundation.

This migration creates only isolated ADS tables.  It does not touch OAuth,
traffic attribution, campaign settings, or any Direct write surface.
"""
from alembic import op
import sqlalchemy as sa

revision = "0046_ads_v1_foundation"
down_revision = "0045_admin_onboarding_seen"
branch_labels = None
depends_on = None


def _id():
    return sa.String(length=36)


def upgrade():
    op.create_table("ads_accounts", sa.Column("id", _id(), primary_key=True), sa.Column("admin_id", _id(), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False), sa.Column("provider", sa.String(32), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("admin_id", "provider", name="uq_ads_account_provider"))
    op.create_index("ix_ads_accounts_admin_id", "ads_accounts", ["admin_id"])
    op.create_table("ads_campaigns", sa.Column("id", _id(), primary_key=True), sa.Column("account_id", _id(), sa.ForeignKey("ads_accounts.id", ondelete="CASCADE"), nullable=False), sa.Column("external_id", sa.String(64), nullable=False), sa.Column("name", sa.String(512), nullable=False), sa.Column("state", sa.String(64)), sa.Column("status", sa.String(64)), sa.Column("raw_json", sa.Text()), sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("account_id", "external_id", name="uq_ads_campaign_external"))
    op.create_index("ix_ads_campaigns_account_id", "ads_campaigns", ["account_id"])
    op.create_index("ix_ads_campaigns_external_id", "ads_campaigns", ["external_id"])
    op.create_table("ads_channel_campaign_links", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False), sa.Column("campaign_id", _id(), sa.ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("channel_id", "campaign_id", name="uq_ads_channel_campaign"))
    op.create_index("ix_ads_channel_campaign_links_channel_id", "ads_channel_campaign_links", ["channel_id"])
    op.create_table("ads_snapshots", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False), sa.Column("campaign_id", _id(), sa.ForeignKey("ads_campaigns.id", ondelete="SET NULL")), sa.Column("source", sa.String(32), nullable=False), sa.Column("period_from", sa.Date()), sa.Column("period_to", sa.Date()), sa.Column("facts_json", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_ads_snapshots_channel_id", "ads_snapshots", ["channel_id"])
    op.create_table("ads_sync_runs", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("trigger", sa.String(32), nullable=False), sa.Column("summary_json", sa.Text()), sa.Column("error_code", sa.String(96)), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_table("ads_ai_runs", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("provider", sa.String(64)), sa.Column("model", sa.String(128)), sa.Column("input_snapshot_id", _id(), sa.ForeignKey("ads_snapshots.id", ondelete="SET NULL")), sa.Column("output_json", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("ads_recommendations", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False), sa.Column("ai_run_id", _id(), sa.ForeignKey("ads_ai_runs.id", ondelete="SET NULL")), sa.Column("status", sa.String(24), nullable=False), sa.Column("priority", sa.String(16), nullable=False), sa.Column("title", sa.String(512), nullable=False), sa.Column("rationale", sa.Text()), sa.Column("evidence_ids_json", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("ads_memories", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE")), sa.Column("scope", sa.String(24), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("kind", sa.String(64), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("evidence_ids_json", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("ads_data_health", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("reasons_json", sa.Text(), nullable=False), sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("ads_actions", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False), sa.Column("recommendation_id", _id(), sa.ForeignKey("ads_recommendations.id", ondelete="SET NULL")), sa.Column("action_type", sa.String(64), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("outcome_json", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("ads_experiments", sa.Column("id", _id(), primary_key=True), sa.Column("channel_id", _id(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("hypothesis", sa.Text(), nullable=False), sa.Column("result_json", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for table in ("ads_experiments", "ads_actions", "ads_data_health", "ads_memories", "ads_recommendations", "ads_ai_runs", "ads_sync_runs", "ads_snapshots", "ads_channel_campaign_links", "ads_campaigns", "ads_accounts"):
        op.drop_table(table)
