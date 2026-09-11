from pathlib import Path

from app.db.models import AdsPromotionCampaignLink


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "alembic/versions/0074_ads_provider_neutral_links.py").read_text()
PERIODIC = (ROOT / "app/ads/periodic.py").read_text()
SERVICE = (ROOT / "app/ads/service.py").read_text()


def test_generic_link_is_strictly_channel_or_bot_scoped():
    table = AdsPromotionCampaignLink.__table__
    assert {foreign.column.table.name for column in table.columns for foreign in column.foreign_keys} >= {
        "channels", "bot_promotion_profiles", "ads_campaigns"
    }
    check = next(item for item in table.constraints if item.name == "ck_ads_promotion_campaign_link_one_object")
    assert "object_type = 'CHANNEL'" in str(check.sqltext)
    assert "object_type = 'BOT'" in str(check.sqltext)
    assert "channel_id IS NOT NULL" in str(check.sqltext)
    assert "bot_promotion_profile_id IS NOT NULL" in str(check.sqltext)


def test_migration_preserves_legacy_links_and_adds_exact_maxibot_identity():
    assert 'revision = "0074_ads_provider_neutral_links"' in MIGRATION
    assert 'down_revision = "0073_free_trial_v1"' in MIGRATION
    assert "FROM ads_channel_campaign_links" in MIGRATION
    assert "status" in MIGRATION
    assert "713929699" in MIGRATION
    assert "bp.bot_key = 'maxibot'" in MIGRATION
    assert "a.provider = 'YANDEX_DIRECT'" in MIGRATION
    assert "c.name" not in MIGRATION[MIGRATION.index("# Link MaxiBot"):]


def test_collection_scope_is_active_generic_links_not_canary_names():
    assert "AdsPromotionCampaignLink" in PERIODIC
    assert "AdsChannelCampaignLink" not in PERIODIC
    assert "ads_periodic_canary_campaign_ids" not in PERIODIC
    assert "AdsPromotionCampaignLink.status == \"ACTIVE\"" in PERIODIC
    assert "AdsAccount.provider == \"YANDEX_DIRECT\"" in PERIODIC
    assert "AdsAccount.status == \"CONNECTED\"" in PERIODIC
    assert "AdsCampaign.state == \"ON\"" in PERIODIC


def test_bot_scope_uses_generic_link_and_preserves_channel_ai_selection():
    assert "async def campaigns_for_promotion_object" in SERVICE
    assert "async def campaigns_for_bot" in SERVICE
    assert 'object_type="BOT"' in SERVICE
    assert "AdsPromotionCampaignLink.object_type == \"CHANNEL\"" in SERVICE
    assert "Campaigns.update" not in SERVICE


def test_search_query_and_cadence_contract_remain_unchanged():
    config = (ROOT / "app/core/config.py").read_text()
    assert "ads_collection_campaign_interval_minutes: int = Field(default=60" in config
    assert "ads_collection_granular_interval_minutes: int = Field(default=360" in config
    assert "ads_collection_entity_sync_interval_minutes: int = Field(default=1440" in config
    assert "ads_collection_search_query_enabled: bool = Field(default=False" in config
    assert "BOT/CHANNEL runtime semantics" in (ROOT / "docs/ADS_DATA_MODEL.md").read_text()
