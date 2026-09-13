from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OAUTH = (ROOT / "app/integrations/yandex_oauth.py").read_text()
MAIN = (ROOT / "app/main.py").read_text()
MIGRATION = (ROOT / "alembic/versions/0092_split_yandex_oauth_credentials.py").read_text()


def test_service_credentials_and_cache_keys_are_separate():
    assert "yandex:oauth:metrica:token:" in OAUTH
    assert "yandex:oauth:direct:token:" in OAUTH
    assert "get_direct_encrypted_token" in OAUTH
    assert "yandex_direct_oauth_token_encrypted" in OAUTH
    assert "yandex_metrica_oauth_token_encrypted" in OAUTH


def test_oauth_start_paths_mark_the_service():
    direct = MAIN[MAIN.index("async def yandex_direct_oauth_start"):MAIN.index("async def yandex_system_oauth_start")]
    metrica = MAIN[MAIN.index("async def yandex_oauth_start"):MAIN.index("async def yandex_direct_oauth_start")]
    assert 'create_state(admin.external_id, channel.id, service="direct")' in direct
    assert 'create_state(admin.external_id, channel.id, service="metrica")' in metrica
    assert 'authorize_url(state, scopes=direct_scope())' in direct


def test_channel_direct_entry_uses_direct_start_and_preserves_channel_context():
    menu = (ROOT / "app/bot/menu.py").read_text()
    screen = menu[menu.index('if screen.startswith("promotion_yandex:")'):menu.index('if screen.startswith("promotion_settings:")')]
    assert 'yandex_direct_oauth_url(admin.external_id, selected_id)' in screen
    assert 'oauth/yandex/direct/start' in menu
    assert 'channel_id={quote(channel_id, safe=\'\')}' in menu


def test_callback_dispatches_direct_state_to_direct_writer_only():
    direct_callback = MAIN[MAIN.index('@app.get("/oauth/yandex/callback"'):MAIN.index('async def _landing_page')]
    assert 'oauth_service = flow.get("service") or "metrica"' in direct_callback
    assert 'save_token(flow["admin"], flow["channel"], payload, db=db, service=oauth_service)' in direct_callback
    assert 'if flow.get("service") == "direct"' in direct_callback
    assert 'if oauth_service == "metrica"' in direct_callback


def test_split_migration_does_not_add_ads_tables():
    assert 'op.add_column("traffic_profiles"' in MIGRATION
    assert "ads_" not in MIGRATION
