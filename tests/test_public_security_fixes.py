"""Regression coverage for public MAX and OAuth ingress boundaries."""

from pathlib import Path
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def temporary_uploads_directory(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))


def test_invalid_dedicated_fernet_key_is_rejected(monkeypatch):
    from app import security_tokens
    from app.core.config import settings

    monkeypatch.setattr(settings, "custom_bot_token_encryption_key", "not-a-fernet-key")
    monkeypatch.setattr(settings, "app_secret", "a sufficiently long secret for tests")
    with pytest.raises(ValueError, match="CUSTOM_BOT_TOKEN_ENCRYPTION_KEY"):
        security_tokens.encrypt_token("token")


def test_real_mode_rejects_weak_application_secret():
    from app.security_tokens import validate_crypto_configuration

    with pytest.raises(RuntimeError, match="APP_SECRET"):
        validate_crypto_configuration(real_mode=True, app_secret="dev-secret", encryption_key=None)


@pytest.mark.parametrize("weak_secret", [
    " " * 48,
    "x" * 48,
    "ab" * 24,
    "dev-secret-2026-varying-XYZ789!?",
    "replace-me-ForProduction-2026_XYZ!",
])
def test_real_mode_rejects_low_variety_or_placeholder_application_secret(weak_secret):
    from app.security_tokens import validate_crypto_configuration

    with pytest.raises(RuntimeError, match="APP_SECRET"):
        validate_crypto_configuration(real_mode=True, app_secret=weak_secret, encryption_key=None)


def test_real_mode_accepts_varied_application_secret_but_stub_mode_keeps_dev_secret():
    from app.security_tokens import validate_crypto_configuration

    validate_crypto_configuration(
        real_mode=True,
        app_secret="Q7!v3K#t9M@x5P$z2L^b8N&h4R?c6D+Aa1",
        encryption_key=None,
    )
    validate_crypto_configuration(real_mode=False, app_secret="dev-secret", encryption_key=None)


def test_strong_legacy_derived_fernet_key_still_round_trips(monkeypatch):
    from app import security_tokens
    from app.core.config import settings

    monkeypatch.setattr(settings, "custom_bot_token_encryption_key", None)
    monkeypatch.setattr(settings, "app_secret", "Q7!v3K#t9M@x5P$z2L^b8N&h4R?c6D+Aa1")
    encrypted = security_tokens.encrypt_token("legacy token")
    assert security_tokens.decrypt_token(encrypted) == "legacy token"


@pytest.mark.asyncio
async def test_oauth_callback_state_is_consumed_atomically_once(monkeypatch):
    from app.integrations import yandex_oauth

    class MemoryRedis:
        def __init__(self):
            self.values = {"yandex:oauth:state:state-token": '{"admin":"owner-1","channel":"channel-1"}'}

        async def getdel(self, key):
            return self.values.pop(key, None)

        async def aclose(self):
            return None

    redis = MemoryRedis()
    monkeypatch.setattr(yandex_oauth, "_redis", lambda: redis)
    first = await yandex_oauth.consume_state("state-token")
    assert first == {"admin": "owner-1", "channel": "channel-1"}
    assert await yandex_oauth.consume_state("state-token") is None


def test_oauth_start_routes_do_not_accept_caller_selected_identity():
    source = (ROOT / "app/main.py").read_text()
    for name, next_name in (
        ("yandex_oauth_start", "yandex_direct_oauth_start"),
        ("yandex_direct_oauth_start", "yandex_system_oauth_start"),
        ("yandex_system_oauth_start", "yandex_bot_oauth_start"),
    ):
        section = source[source.index(f"async def {name}"):source.index(f"async def {next_name}")]
        assert "ticket:" in section or "consume_oauth_ticket" in section
        assert "admin_id: str" not in section


def test_public_message_requires_verified_max_identity_and_limits_ingress():
    source = (ROOT / "app/main.py").read_text()
    section = source[source.index("async def public_message"):source.index("@app.get(\"/oauth/yandex/start\")")]
    assert "X-MAX-Init-Data" in section
    assert "validate_max_init_data" in section
    assert "enforce_public_message_rate_limit" in section
    assert "compare_digest(user_id.encode(\"utf-8\")" in section
    assert "HTTPException(403" in section


def test_webhook_requires_secret_and_constant_time_comparison():
    source = (ROOT / "app/main.py").read_text()
    section = source[source.index("async def max_webhook"):source.index("async def ", source.index("async def max_webhook") + 10)]
    assert "compare_digest" in section
    assert "max_webhook_secret" in section
    assert 'settings.max_updates_mode.lower() != "webhook"' in section


def test_crypto_configuration_is_checked_before_database_and_polling():
    main = (ROOT / "app/main.py").read_text()
    lifespan = main[main.index("async def lifespan"):main.index("app = FastAPI")]
    assert lifespan.index("validate_crypto_settings") < lifespan.index("async with engine.begin()")
    runtime = (ROOT / "app/bot/runtime.py").read_text()
    poller = runtime[runtime.index("async def run_forever"):runtime.index("while not self._stop.is_set()")]
    assert "validate_crypto_settings" in poller
    worker = (ROOT / "app/poller.py").read_text()
    entry = worker[worker.index("async def main"):worker.index("client = RealMaxClient()")]
    assert entry.index("validate_crypto_settings()") < entry.index("async with engine.begin()")


def test_no_argument_crypto_settings_validator_uses_active_settings(monkeypatch):
    from app.core.config import settings
    from app.security_tokens import validate_crypto_settings

    monkeypatch.setattr(settings, "max_client_mode", "real")
    monkeypatch.setattr(settings, "app_secret", "dev-secret")
    monkeypatch.setattr(settings, "custom_bot_token_encryption_key", None)
    with pytest.raises(RuntimeError, match="APP_SECRET"):
        validate_crypto_settings()


def test_invalid_fernet_key_reference_is_not_silently_fallbacked():
    """Keep fallback compatibility only when no dedicated key was configured."""
    source = (ROOT / "app/security_tokens.py").read_text()
    assert "raise ValueError" in source


def test_max_init_data_rejects_non_ascii_signature_without_type_error(monkeypatch):
    from fastapi import HTTPException
    from app.core.config import settings
    from app.security import validate_max_init_data

    monkeypatch.setattr(settings, "max_bot_token", "test-token")
    params = urlencode({"auth_date": str(int(time.time())), "user": json.dumps({"user_id": "юзер-1"}), "hash": "я" * 64})
    with pytest.raises(HTTPException) as error:
        validate_max_init_data(params)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_oauth_initiation_ticket_is_bound_and_consumed_once(monkeypatch):
    from app.core.config import settings
    from app.integrations import yandex_oauth

    class MemoryRedis:
        values = {}

        async def set(self, key, value, *, ex, nx):
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

        async def getdel(self, key):
            return self.values.pop(key, None)

        async def aclose(self):
            return None

    redis = MemoryRedis()
    monkeypatch.setattr(yandex_oauth, "_redis", lambda: redis)
    monkeypatch.setattr(settings, "app_secret", "test-secret-with-sufficient-entropy-2026")
    ticket = await yandex_oauth.issue_oauth_ticket("owner-ю", "bot", "bot-42")
    claims = await yandex_oauth.consume_oauth_ticket(ticket, "bot")
    assert claims["admin"] == "owner-ю"
    assert claims["resource"] == "bot-42"
    assert await yandex_oauth.consume_oauth_ticket(ticket, "bot") is None
    other = await yandex_oauth.issue_oauth_ticket("owner-ю", "bot", "bot-43")
    assert await yandex_oauth.consume_oauth_ticket(other, "system") is None


@pytest.mark.asyncio
async def test_public_message_rate_limit_blocks_sender_and_redis_outage(monkeypatch):
    from fastapi import HTTPException
    from redis.asyncio import Redis
    from app.security import enforce_public_message_rate_limit

    class MemoryRedis:
        global_count = 0
        sender_counts = {}

        async def eval(self, script, key_count, global_key, sender_key, window):
            self.global_count += 1
            count = self.sender_counts.get(sender_key, 0) + 1
            self.sender_counts[sender_key] = count
            return [self.global_count, count]

        async def aclose(self):
            return None

    redis = MemoryRedis()
    monkeypatch.setattr(Redis, "from_url", lambda *args, **kwargs: redis)
    for _ in range(10):
        await enforce_public_message_rate_limit("MAX-юзер-1")
    with pytest.raises(HTTPException) as limited:
        await enforce_public_message_rate_limit("MAX-юзер-1")
    assert limited.value.status_code == 429

    def unavailable(*args, **kwargs):
        raise OSError("redis unavailable")

    monkeypatch.setattr(Redis, "from_url", unavailable)
    with pytest.raises(HTTPException) as outage:
        await enforce_public_message_rate_limit("MAX-юзер-2")
    assert outage.value.status_code == 503


@pytest.mark.asyncio
async def test_public_message_uses_signed_identity_and_rejects_unicode_mismatch(monkeypatch):
    from fastapi import HTTPException
    from app import main

    monkeypatch.setattr(main, "validate_max_init_data", lambda _: {
        "external_id": "MAX-用户-١", "user": {"first_name": "李", "last_name": "小龍"},
    })
    async def allow_rate_limit(_user_id):
        return None

    monkeypatch.setattr(main, "enforce_public_message_rate_limit", allow_rate_limit)

    class FakeDb:
        def __init__(self):
            self.rows = []
            self.scalar_calls = 0

        async def scalar(self, _query):
            self.scalar_calls += 1
            return [type("Channel", (), {"id": "channel-1", "accepting": True, "sent_text": "Thanks"})(), 0, None][self.scalar_calls - 1]

        def add(self, row):
            self.rows.append(row)

        async def flush(self):
            conversation = self.rows[0]
            conversation.id = "conversation-1"
            conversation.unread = 0

        async def commit(self):
            return None

    with pytest.raises(HTTPException) as mismatch:
        await main.public_message("slug", main.MessageIn(text="Hello"), user_id="別人", x_max_init_data="signed", db=FakeDb())
    assert mismatch.value.status_code == 403

    db = FakeDb()
    result = await main.public_message("slug", main.MessageIn(text="Hello"), x_max_init_data="signed", db=db)
    conversation, message = db.rows
    assert result["conversation_id"] == "conversation-1"
    assert conversation.user_external_id == "MAX-用户-١"
    assert conversation.user_name == "李 小龍"
    assert message.text == "Hello"


@pytest.mark.asyncio
async def test_authenticated_promotion_menu_keeps_clickable_ticket_links(monkeypatch):
    from app.bot import menu

    monkeypatch.setattr(menu, "configured", lambda: True)
    monkeypatch.setattr(menu, "is_maxibot_owner", lambda _admin: True)
    monkeypatch.setattr(menu, "system_connection_status", AsyncMock(return_value=False))
    issue_ticket = AsyncMock(side_effect=["system-ticket", "bot-ticket"])
    monkeypatch.setattr(menu, "issue_oauth_ticket", issue_ticket)
    admin = SimpleNamespace(id="db-admin-1", external_id="owner-1")

    class FakeDb:
        def __init__(self, row):
            self.row = row

        async def scalars(self, _query):
            return SimpleNamespace(all=lambda: [])

        async def scalar(self, _query):
            return self.row

    system_profile = SimpleNamespace(yandex_counter_id=None, yandex_goal_name=None)
    _, system_keyboard = await menu.render_screen(FakeDb(system_profile), admin, "promotion_maxibot_yandex")
    system_button = system_keyboard[0][0]
    assert system_button["type"] == "link"
    assert "ticket=system-ticket" in system_button["url"]
    assert "admin_id=" not in system_button["url"]

    bot = SimpleNamespace(id="bot-1", display_name="Test Bot", username="test_bot")
    _, bot_keyboard = await menu.render_screen(FakeDb(bot), admin, "promotion_bot_yandex:bot-1")
    bot_button = bot_keyboard[0][0]
    assert bot_button["type"] == "link"
    assert "ticket=bot-ticket" in bot_button["url"]
    assert "bot_id=" not in bot_button["url"]
    assert issue_ticket.await_args_list[0].args == ("owner-1", "system")
    assert issue_ticket.await_args_list[1].args == ("owner-1", "bot", "bot-1")


@pytest.mark.asyncio
async def test_webhook_endpoint_is_disabled_outside_webhook_mode_and_requires_secret(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app import main
    from app.core.config import settings

    request = Request({"type": "http", "method": "POST", "path": "/max/webhook", "headers": []})
    monkeypatch.setattr(settings, "max_client_mode", "real")
    monkeypatch.setattr(settings, "max_updates_mode", "longpoll")
    monkeypatch.setattr(settings, "max_webhook_secret", "expected-secret")
    monkeypatch.setattr(main, "bot_runtime", object())
    with pytest.raises(HTTPException) as disabled:
        await main.max_webhook(request)
    assert disabled.value.status_code == 503

    monkeypatch.setattr(settings, "max_updates_mode", "webhook")
    monkeypatch.setattr(settings, "max_webhook_secret", None)
    with pytest.raises(HTTPException) as missing:
        await main.max_webhook(request)
    assert missing.value.status_code == 401

    monkeypatch.setattr(settings, "max_webhook_secret", "秘密")
    wrong_secret_request = Request({
        "type": "http", "method": "POST", "path": "/max/webhook",
        "headers": [(b"x-max-bot-api-secret", "ちがう".encode("utf-8"))],
    })
    with pytest.raises(HTTPException) as invalid:
        await main.max_webhook(wrong_secret_request)
    assert invalid.value.status_code == 401


@pytest.mark.asyncio
async def test_webhook_startup_requires_url_and_secret_before_database(monkeypatch):
    from app import main
    from app.core.config import settings

    monkeypatch.setattr(settings, "max_updates_mode", "webhook")
    monkeypatch.setattr(settings, "max_webhook_url", "https://example.invalid/max/webhook")
    monkeypatch.setattr(settings, "max_webhook_secret", None)
    async def unexpected_db_access():
        raise AssertionError("startup reached DB before validating webhook configuration")

    monkeypatch.setattr(main, "engine", SimpleNamespace(begin=unexpected_db_access))
    with pytest.raises(RuntimeError, match="MAX_WEBHOOK_URL and MAX_WEBHOOK_SECRET"):
        async with main.lifespan(main.app):
            pass
