"""Canonicalize the ADS V2 warehouse without touching MaxiBot outcomes."""

from alembic import op
import sqlalchemy as sa


revision = "0075_ads_canonical_v2"
down_revision = "0074_ads_provider_neutral_links"
branch_labels = None
depends_on = None


def _rename_index(old: str, new: str) -> None:
    op.execute(sa.text(f'ALTER INDEX IF EXISTS "{old}" RENAME TO "{new}"'))


def _rename_constraint(table: str, old: str, new: str) -> None:
    op.execute(sa.text(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"'))


def upgrade() -> None:
    # Physical names are changed in place so primary keys, rows and FK values
    # survive. PostgreSQL updates dependent FK targets when a table is renamed.
    for old, new in (
        ("ads_v2_entities", "ads_entities"),
        ("ads_v2_report_runs", "ads_report_runs"),
        ("ads_v2_collection_runs", "ads_collection_runs"),
        ("ads_v2_observations", "ads_observations"),
        ("ads_v2_daily_aggregates", "ads_daily_aggregates"),
        ("ads_v2_outcomes", "ads_ai_outcomes"),
        ("ads_snapshots", "ads_analysis_snapshots"),
    ):
        op.rename_table(old, new)

    for old, new in (
        ("ix_ads_v2_entities_provider", "ix_ads_entities_provider"),
        ("ix_ads_v2_entities_entity_type", "ix_ads_entities_entity_type"),
        ("ix_ads_v2_entities_provider_entity_id", "ix_ads_entities_provider_entity_id"),
        ("ix_ads_v2_entities_parent", "ix_ads_entities_parent"),
        ("ix_ads_v2_report_runs_provider", "ix_ads_report_runs_provider"),
        ("ix_ads_v2_report_runs_campaign", "ix_ads_report_runs_campaign"),
        ("ix_ads_v2_report_runs_request_hash", "ix_ads_report_runs_request_hash"),
        ("ix_ads_v2_report_runs_status", "ix_ads_report_runs_status"),
        ("ix_ads_v2_collection_runs_provider", "ix_ads_collection_runs_provider"),
        ("ix_ads_v2_collection_runs_account", "ix_ads_collection_runs_account"),
        ("ix_ads_v2_collection_runs_campaign", "ix_ads_collection_runs_campaign"),
        ("ix_ads_v2_collection_runs_scheduled", "ix_ads_collection_runs_scheduled"),
        ("ix_ads_v2_collection_runs_status", "ix_ads_collection_runs_status"),
        ("ix_ads_v2_collection_runs_retry", "ix_ads_collection_runs_retry"),
        ("ix_ads_v2_observations_snapshot", "ix_ads_observations_snapshot"),
        ("ix_ads_v2_observations_entity", "ix_ads_observations_entity"),
        ("ix_ads_v2_observations_dimension", "ix_ads_observations_dimension"),
        ("ix_ads_v2_observations_window", "ix_ads_observations_window"),
        ("ix_ads_v2_observations_fact", "ix_ads_observations_fact"),
        ("ix_ads_v2_observations_report", "ix_ads_observations_report"),
        ("ix_ads_v2_observations_collection_run_id", "ix_ads_observations_collection_run_id"),
        ("ix_ads_v2_observations_logical_key", "ix_ads_observations_logical_key"),
        ("ix_ads_v2_daily_aggregates_provider", "ix_ads_daily_aggregates_provider"),
        ("ix_ads_v2_daily_aggregates_campaign", "ix_ads_daily_aggregates_campaign"),
        ("ix_ads_v2_daily_aggregates_entity", "ix_ads_daily_aggregates_entity"),
        ("ix_ads_v2_daily_aggregates_date", "ix_ads_daily_aggregates_date"),
        ("ix_ads_v2_daily_aggregates_created", "ix_ads_daily_aggregates_created"),
        ("ix_ads_v2_outcomes_recommendation", "ix_ads_ai_outcomes_recommendation"),
        ("ix_ads_v2_outcomes_due", "ix_ads_ai_outcomes_due"),
        ("ix_ads_v2_outcomes_status", "ix_ads_ai_outcomes_status"),
        ("ix_ads_v2_outcomes_baseline", "ix_ads_ai_outcomes_baseline"),
        ("ix_ads_v2_outcomes_next_retry_at", "ix_ads_ai_outcomes_next_retry_at"),
        ("ix_ads_snapshots_channel_id", "ix_ads_analysis_snapshots_channel_id"),
        ("ix_ads_snapshots_created_at", "ix_ads_analysis_snapshots_created_at"),
    ):
        _rename_index(old, new)

    for table, old, new in (
        ("ads_entities", "uq_ads_v2_entity_identity", "uq_ads_entity_identity"),
        ("ads_daily_aggregates", "uq_ads_v2_daily_aggregate_identity", "uq_ads_daily_aggregate_identity"),
        ("ads_ai_outcomes", "uq_ads_v2_outcome_horizon", "uq_ads_ai_outcome_horizon"),
    ):
        _rename_constraint(table, old, new)

    # Canonical account/campaign metadata is additive and nullable where the
    # provider did not return a value. Existing fields remain readable during
    # the compatibility window and are copied into the normalized fields.
    for table, column in (
        ("ads_accounts", sa.Column("external_account_id", sa.String(128))),
        ("ads_accounts", sa.Column("display_name", sa.String(255))),
        ("ads_accounts", sa.Column("connection_status", sa.String(24))),
        ("ads_accounts", sa.Column("provider_metadata_json", sa.Text(), server_default="{}")),
        ("ads_accounts", sa.Column("metadata_updated_at", sa.DateTime(timezone=True))),
        ("ads_campaigns", sa.Column("ads_account_id", sa.String(36), sa.ForeignKey("ads_accounts.id", ondelete="CASCADE"))),
        ("ads_campaigns", sa.Column("external_campaign_id", sa.String(64))),
        ("ads_campaigns", sa.Column("campaign_type", sa.String(64))),
        ("ads_campaigns", sa.Column("start_date", sa.Date())),
        ("ads_campaigns", sa.Column("end_date", sa.Date())),
        ("ads_campaigns", sa.Column("strategy_type", sa.String(128))),
        ("ads_campaigns", sa.Column("budget_type", sa.String(64))),
        ("ads_campaigns", sa.Column("daily_budget", sa.Numeric(20, 6))),
        ("ads_campaigns", sa.Column("weekly_budget", sa.Numeric(20, 6))),
        ("ads_campaigns", sa.Column("currency", sa.String(16))),
        ("ads_campaigns", sa.Column("optimization_goal", sa.String(128))),
        ("ads_campaigns", sa.Column("target_cpa", sa.Numeric(20, 6))),
        ("ads_campaigns", sa.Column("target_roi", sa.Numeric(20, 6))),
        ("ads_campaigns", sa.Column("target_conversion_value", sa.Numeric(20, 6))),
        ("ads_campaigns", sa.Column("settings_json", sa.Text())),
        ("ads_campaigns", sa.Column("provider_metadata_json", sa.Text(), server_default="{}")),
        ("ads_campaigns", sa.Column("config_hash", sa.String(64))),
        ("ads_campaigns", sa.Column("metadata_updated_at", sa.DateTime(timezone=True))),
    ):
        op.add_column(table, column)
    op.create_index("ix_ads_campaigns_ads_account_id", "ads_campaigns", ["ads_account_id"])
    op.create_index("ix_ads_campaigns_external_campaign_id", "ads_campaigns", ["external_campaign_id"])
    op.execute(sa.text("UPDATE ads_accounts SET connection_status = status, metadata_updated_at = updated_at WHERE connection_status IS NULL"))
    op.execute(sa.text("UPDATE ads_campaigns SET ads_account_id = account_id, external_campaign_id = external_id WHERE ads_account_id IS NULL"))
    op.execute(sa.text("UPDATE ads_campaigns SET provider_metadata_json = COALESCE(raw_json, '{}'), config_hash = md5(COALESCE(raw_json, '{}')), metadata_updated_at = last_seen_at WHERE config_hash IS NULL"))

    # Canonical entity identity/content fields. The existing provider fields
    # are retained as compatibility columns while all new writes can use the
    # normalized fields.
    op.add_column("ads_entities", sa.Column("campaign_id", sa.String(36), sa.ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=True))
    op.add_column("ads_entities", sa.Column("external_entity_id", sa.String(128), nullable=True))
    op.add_column("ads_entities", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("ads_entities", sa.Column("metadata_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_ads_entities_campaign_id", "ads_entities", ["campaign_id"])
    op.execute(sa.text("UPDATE ads_entities SET external_entity_id = provider_entity_id, metadata_updated_at = last_seen_at WHERE external_entity_id IS NULL"))

    # One operational history replaces the manual sync table. Manual rows are
    # copied with their IDs/status/timestamps; no history is discarded.
    op.add_column("ads_collection_runs", sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=True))
    op.add_column("ads_collection_runs", sa.Column("run_type", sa.String(48), nullable=True))
    op.add_column("ads_collection_runs", sa.Column("trigger", sa.String(32), nullable=True))
    op.add_column("ads_collection_runs", sa.Column("summary_json", sa.Text(), nullable=True))
    op.alter_column("ads_collection_runs", "account_id", nullable=True)
    op.alter_column("ads_collection_runs", "campaign_id", nullable=True)
    op.execute(sa.text("UPDATE ads_collection_runs SET run_type = collection_type, trigger = 'PERIODIC' WHERE run_type IS NULL"))
    op.execute(sa.text(
        """
        INSERT INTO ads_collection_runs
            (id, provider, account_id, campaign_id, channel_id, collection_type,
             run_type, trigger, scheduled_at, slot_key, status, started_at,
             completed_at, attempts, error_code, summary_json)
        SELECT id, 'INTERNAL', NULL, NULL, channel_id, 'MANUAL_AI_SYNC',
               'MANUAL_AI_SYNC', trigger, started_at,
               'manual-ai-sync:' || id, status, started_at, finished_at,
               1, error_code, summary_json
        FROM ads_sync_runs
        WHERE NOT EXISTS (SELECT 1 FROM ads_collection_runs existing WHERE existing.id = ads_sync_runs.id)
        """
    ))
    op.drop_table("ads_sync_runs")

    # The generic link table is now the sole object-to-campaign relation.
    op.execute(sa.text(
        """
        DO $$
        DECLARE source_count bigint; migrated_count bigint; source_active bigint; migrated_active bigint;
        BEGIN
          SELECT count(*) INTO source_count FROM ads_channel_campaign_links;
          SELECT count(*) INTO migrated_count FROM ads_promotion_campaign_links WHERE object_type = 'CHANNEL';
          SELECT count(*) INTO source_active FROM ads_channel_campaign_links WHERE status = 'ACTIVE';
          SELECT count(*) INTO migrated_active FROM ads_promotion_campaign_links WHERE object_type = 'CHANNEL' AND status = 'ACTIVE';
          IF source_count <> migrated_count OR source_active <> migrated_active THEN
            RAISE EXCEPTION 'CHANNEL_LINK_MIGRATION_MISMATCH source=% migrated=% active_source=% active_migrated=%', source_count, migrated_count, source_active, migrated_active;
          END IF;
        END $$
        """
    ))
    op.drop_table("ads_channel_campaign_links")

    # Creative content is normalized separately from generic entities. Binary
    # media is intentionally not stored.
    op.create_table(
        "ads_creatives",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ad_entity_id", sa.String(36), sa.ForeignKey("ads_entities.id", ondelete="SET NULL")),
        sa.Column("external_creative_id", sa.String(128)),
        sa.Column("creative_type", sa.String(64)),
        sa.Column("title", sa.Text()), sa.Column("title2", sa.Text()),
        sa.Column("body", sa.Text()), sa.Column("cta", sa.String(512)),
        sa.Column("href", sa.String(2048)), sa.Column("display_url", sa.String(2048)),
        sa.Column("image_ref", sa.String(2048)), sa.Column("video_ref", sa.String(2048)),
        sa.Column("provider_metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("metadata_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("campaign_id", "external_creative_id", name="uq_ads_creative_external"),
    )
    op.create_index("ix_ads_creatives_campaign_id", "ads_creatives", ["campaign_id"])
    op.create_index("ix_ads_creatives_ad_entity_id", "ads_creatives", ["ad_entity_id"])
    op.create_index("ix_ads_creatives_content_hash", "ads_creatives", ["content_hash"])

    # These two tables have no runtime references. Fail closed if data exists;
    # a later migration must explicitly preserve it instead of dropping it.
    op.execute(sa.text("DO $$ BEGIN IF EXISTS (SELECT 1 FROM ads_actions) THEN RAISE EXCEPTION 'ADS_ACTIONS_NOT_EMPTY'; END IF; IF EXISTS (SELECT 1 FROM ads_experiments) THEN RAISE EXCEPTION 'ADS_EXPERIMENTS_NOT_EMPTY'; END IF; END $$"))
    op.drop_table("ads_actions")
    op.drop_table("ads_experiments")


def downgrade() -> None:
    raise RuntimeError("0075 canonical ADS migration is forward-only; restore from release backup")
