from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OAUTH = (ROOT / "app/integrations/yandex_oauth.py").read_text()
MODELS = (ROOT / "app/db/models.py").read_text()
COMPOSE = (ROOT / "docker-compose.vps.yml").read_text()
SENDER = (ROOT / "app/bot/menu.py").read_text()


def test_yandex_oauth_is_durable_and_redis_is_cache_only():
    assert "yandex_oauth_token_encrypted" in MODELS
    assert "Redis is a best-effort cache only" in OAUTH
    assert "profile.yandex_oauth_token_encrypted = encrypted" in OAUTH
    assert "if db is None:" in OAUTH


def test_redis_is_persistent_but_not_published_to_the_host():
    redis_block = COMPOSE[COMPOSE.index("  redis:\n"):COMPOSE.index("  https:\n")]
    assert "maxibot_redis_data:/data" in redis_block
    assert '"--appendonly", "yes"' in redis_block
    assert "ports:" not in redis_block


def test_missing_oauth_or_counter_blocks_without_keyerror():
    sender = SENDER[SENDER.index("async def _send_exact_conversion"):SENDER.index("def home_menu_banner_media")]
    assert 'conversion.last_error = "yandex_oauth_missing"' in sender
    assert 'conversion.last_error = "yandex_counter_missing"' in sender
    assert 'setup["counter_id"]' not in sender
