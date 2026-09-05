from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app/main.py").read_text(encoding="utf-8")
MENU = (ROOT / "app/bot/menu.py").read_text(encoding="utf-8")
MINI = (ROOT / "miniapp/traffic.html").read_text(encoding="utf-8")
MODELS = (ROOT / "app/db/models.py").read_text(encoding="utf-8")


def _resolve_section() -> str:
    return MAIN[MAIN.index("async def traffic_resolve"):MAIN.index("async def traffic_channel_open")]


def test_bot_destination_is_explicit_and_channel_default_is_preserved():
    assert 'destination_type: Mapped[str] = mapped_column(String(16), default="BOT"' in MODELS
    assert 'destination_type: Mapped[str] = mapped_column(String(16), default="CHANNEL"' in MODELS
    assert 'revision = "0066_shared_bot_handoff"' in (ROOT / "alembic/versions/0066_shared_bot_handoff_destination.py").read_text()


def test_bot_resolve_requires_verified_identity_and_correct_system_destination():
    section = _resolve_section()
    assert "validate_max_init_data(x_max_init_data or \"\")" in section
    assert "SystemTrafficVisit.token == system_token" in section
    assert 'system_visit.destination_type != "BOT"' in section
    assert 'SYSTEM_BOT_HANDOFF_CONFIRMED' in section
    assert 'goal == "bot_started"' in section


def test_bot_conversion_queue_is_unique_and_yclid_is_not_required_for_internal_event():
    section = _resolve_section()
    assert 'UniqueConstraint("visit_id", "goal"' in MODELS
    assert 'if not conversion:' in section
    assert 'SystemConversion(visit_id=system_visit.id, max_user_id=user_id, goal="bot_started"' in section
    assert "yclid" not in section


def test_raw_bot_start_is_legacy_only_and_not_an_ads_conversion():
    start = MENU[MENU.index("async def bind_system_startapp"):MENU.index("async def queue_system_channel_connected")]
    assert 'visit.status = "LEGACY_BOT_START"' in start
    assert "SystemConversion" not in start
    assert "verified Mini App resolve" in start


def test_mini_app_resolves_bot_without_touching_channel_open_branch():
    resolve = MINI[MINI.index("async function resolveChannel"):MINI.index("resolveChannel().catch")]
    assert "data.destination_type === 'BOT'" in resolve
    assert "webApp.close()" in resolve
    channel = MINI[MINI.index("function openChannel"):MINI.index("async function resolveChannel")]
    assert "window.location.replace(targetUrl)" in channel
    assert "webApp.openMaxLink(targetUrl)" in channel


def test_required_safe_handoff_event_names_are_present_without_raw_identity_logging():
    for event in (
        "SYSTEM_LANDING_RENDERED", "SYSTEM_REDIRECT_ATTEMPTED", "MAX_STARTAPP_HANDOFF",
        "MINI_APP_LOADED", "HANDOFF_CONFIRMED", "TOKEN_RESOLVED", "MAX_USER_BOUND",
        "SYSTEM_BOT_HANDOFF_CONFIRMED", "BOT_STARTED_CONVERSION_QUEUED",
    ):
        assert event in MAIN
    assert "raw initData" not in MAIN.lower()
