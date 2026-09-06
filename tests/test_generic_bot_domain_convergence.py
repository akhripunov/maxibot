from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MENU = (ROOT / "app" / "bot" / "menu.py").read_text()
MODELS = (ROOT / "app" / "db" / "models.py").read_text()
MAIN = (ROOT / "app" / "main.py").read_text()
OAUTH = (ROOT / "app" / "integrations" / "yandex_oauth.py").read_text()
MIGRATION = (ROOT / "alembic" / "versions" / "0072_canonical_bot_promotion.py").read_text()


def test_canonical_bot_promotion_model_represents_system_and_customer_bots():
    assert 'class BotPromotionProfile(Base):' in MODELS
    assert '__tablename__ = "bot_promotion_profiles"' in MODELS
    assert 'bot_type: Mapped[str]' in MODELS
    assert 'bot_connection_id: Mapped[Optional[str]]' in MODELS
    assert 'legacy_system_key: Mapped[Optional[str]]' in MODELS
    assert 'class BotConnection(Base):' in MODELS


def test_system_profile_is_compatibility_storage_not_canonical_promotion_lookup():
    assert 'select(BotPromotionProfile)' in MENU
    assert 'select(BotPromotionProfile)' in OAUTH
    assert 'select(BotPromotionProfile)' in MAIN
    assert 'select(SystemPromotionProfile)' not in OAUTH
    assert 'select(SystemPromotionProfile)' not in MAIN


def test_one_shared_bot_promotion_renderer_and_no_new_top_level_navigation():
    assert MENU.count('def render_bot_promotion(') == 1
    assert 'render_bot_promotion(target)' in MENU
    assert 'if screen.startswith("promotion_bot:")' in MENU
    assert 'if screen in {"promotion_maxibot", "promotion_bot:system"}' in MENU
    assert '"🤖 Мои боты", "my_bots"' not in MENU
    home = MENU[MENU.index('if screen == "home":'):MENU.index('if screen == "ads_storage":')]
    assert '"📚 Мои каналы", "promotion"' in home
    assert '"my_bots"' not in home


def test_bot_promotion_is_bot_scoped_and_channel_domain_is_not_used_as_fallback():
    helper = MENU[MENU.index('async def _bot_promotion_target'):MENU.index('async def traffic_stats')]
    assert 'BotPromotionProfile.bot_connection_id == bot.id' in helper
    assert 'TrafficProfile' not in helper
    assert 'LandingPage' not in helper
    assert 'Channel.bot_connection_id' not in helper


def test_legacy_maxibot_landing_identity_and_paid_flow_stay_on_shared_bot_tables():
    assert 'BotTrafficVisit(token=token, bot_id=settings.max_bot_id' in MAIN
    assert 'BotMaxIdentityLink' in MAIN
    assert 'build_native_miniapp_deep_link' in MAIN
    assert 'bot_started' in MAIN
    assert 'bot_conversion_dedupe' in MENU


def test_convergence_migration_preserves_legacy_rows_and_seeds_canonical_profiles():
    assert 'revision = "0072_canonical_bot_promotion"' in MIGRATION
    assert 'down_revision = "0071_bot_paid_dedupe"' in MIGRATION
    assert 'INSERT INTO bot_promotion_profiles' in MIGRATION
    assert 'FROM system_promotion_profiles' in MIGRATION
    assert 'FROM bot_connections bc' in MIGRATION
    assert 'op.drop_table("bot_promotion_profiles")' in MIGRATION
    assert 'DELETE FROM system_promotion_profiles' not in MIGRATION
