from pathlib import Path

from app.core.config import settings
from app.db.base import Base


def test_canonical_retention_defaults_are_centralized():
    assert settings.ads_observations_retention_days == 60
    assert settings.ads_operational_retention_days == 90
    assert settings.ads_data_health_retention_days == 90
    assert settings.ads_hourly_retention_days == settings.ads_observations_retention_days
    assert settings.ads_daily_retention_days == 0


def test_model_metadata_contains_exactly_sixteen_canonical_ads_tables():
    tables = {name for name in Base.metadata.tables if name.startswith("ads_")}
    assert len(tables) == 16
    assert "ads_actions" not in tables
    assert "ads_experiments" not in tables


def test_lifecycle_migration_is_additive_and_index_only():
    migration = Path("alembic/versions/0077_ads_storage_lifecycle_v1.py").read_text()
    assert 'down_revision = "0076_ads_directologist_decision_engine_v1"' in migration
    assert 'op.alter_column("ads_observations", "snapshot_id"' in migration
    assert 'op.create_index("ix_ads_observations_date_to"' in migration
    assert 'op.create_index("ix_ads_collection_runs_completed_at"' in migration
    assert 'op.create_index("ix_ads_report_runs_fetched_at"' in migration
    assert "create_table" not in migration


def test_periodic_collection_does_not_create_analysis_snapshots():
    source = Path("app/ads/periodic.py").read_text()
    campaign = source[source.index("async def collect_campaign_slot"):source.index("async def collect_granular_slot")]
    granular = source[source.index("async def collect_granular_slot"):source.index("async def collect_entity_configuration")]
    assert "AdsAnalysisSnapshot(" not in campaign
    assert "AdsAnalysisSnapshot(" not in granular
    assert "snapshot=None" in campaign
    assert "snapshot=None" in granular


def test_storage_policy_preserves_ai_and_daily_history():
    source = Path("app/ads/storage_governance.py").read_text()
    assert "AdsDailyAggregate" in source
    assert "AdsAiOutcome" not in source
    assert "LONG_TERM" in source
    assert "RETENTION" in source
