from pathlib import Path

from app.db.models import (
    AdsAiOutcome,
    AdsAnalysisSnapshot,
    AdsCollectionRun,
    AdsCreative,
    AdsDailyAggregate,
    AdsEntity,
    AdsObservation,
    AdsPromotionCampaignLink,
    AdsReportRun,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "alembic/versions/0075_ads_canonical_v2.py").read_text()


def test_canonical_ads_physical_names_are_v2_free():
    assert {
        AdsPromotionCampaignLink.__tablename__,
        AdsEntity.__tablename__,
        AdsCreative.__tablename__,
        AdsObservation.__tablename__,
        AdsDailyAggregate.__tablename__,
        AdsCollectionRun.__tablename__,
        AdsReportRun.__tablename__,
        AdsAnalysisSnapshot.__tablename__,
        AdsAiOutcome.__tablename__,
    } == {
        "ads_promotion_campaign_links", "ads_entities", "ads_creatives",
        "ads_observations", "ads_daily_aggregates", "ads_collection_runs",
        "ads_report_runs", "ads_analysis_snapshots", "ads_ai_outcomes",
    }


def test_canonical_migration_is_forward_only_and_fail_closed():
    assert 'revision = "0075_ads_canonical_v2"' in MIGRATION
    assert 'down_revision = "0074_ads_provider_neutral_links"' in MIGRATION
    assert "CHANNEL_LINK_MIGRATION_MISMATCH" in MIGRATION
    assert "ADS_ACTIONS_NOT_EMPTY" in MIGRATION
    assert "ADS_EXPERIMENTS_NOT_EMPTY" in MIGRATION
    assert 'op.drop_table("ads_sync_runs")' in MIGRATION
    assert 'op.drop_table("ads_channel_campaign_links")' in MIGRATION


def test_collection_scope_is_generic_link_driven():
    periodic = (ROOT / "app/ads/periodic.py").read_text()
    migration_0074 = (ROOT / "alembic/versions/0074_ads_provider_neutral_links.py").read_text()
    assert "AdsPromotionCampaignLink.status == \"ACTIVE\"" in periodic
    assert "AdsAccount.status == \"CONNECTED\"" in periodic
    assert "AdsCampaign.state != \"ARCHIVED\"" in periodic
    assert "713929699" in migration_0074
    assert "bp.bot_key = 'maxibot'" in migration_0074


def test_canonical_migration_preserves_ai_snapshot_and_outcome_links():
    assert '"ads_snapshots", "ads_analysis_snapshots"' in MIGRATION
    assert 'ForeignKey("ads_analysis_snapshots.id"' in (ROOT / "app/db/models.py").read_text()
    assert 'ads_ai_outcomes' in MIGRATION
    assert 'AdsAiOutcome' in (ROOT / "app/db/models.py").read_text()
    assert 'TrafficVisit' not in (ROOT / "alembic/versions/0075_ads_canonical_v2.py").read_text()
