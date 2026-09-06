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


def test_generic_bot_attribution_requires_bot_and_destination_identity():
    assert 'class BotTrafficVisit(Base):' in MODELS
    assert 'bot_id: Mapped[str]' in MODELS
    assert 'destination_id: Mapped[str]' in MODELS
    assert 'class BotMaxIdentityLink(Base):' in MODELS
    migration = (ROOT / "alembic/versions/0068_generic_bot_traffic.py").read_text()
    assert 'down_revision = "0067_system_max_identity_links"' in migration
    assert "system_traffic_visits" in migration
    assert "DROP TABLE system_traffic_visits" not in migration.upper()


def test_bot_resolve_requires_verified_identity_and_correct_system_destination():
    section = _resolve_section()
    assert "validate_max_init_data(x_max_init_data or \"\")" in section
    assert "BotTrafficVisit.token == system_token" in section
    assert 'bot_visit.bot_id != settings.max_bot_id' in section
    assert 'SYSTEM_BOT_HANDOFF_CONFIRMED' in section
    assert 'bot_visit.status = "HANDOFF_CONFIRMED"' in section
    assert "BotMaxIdentityLink(" in section
    assert 'BotConversion(' not in section


def test_bot_conversion_queue_is_unique_and_yclid_is_not_required_for_internal_event():
    section = _resolve_section()
    assert 'UniqueConstraint("bot_visit_id", "internal_event"' in MODELS
    assert 'UniqueConstraint("paid_journey_key"' in MODELS
    start = MENU[MENU.index("async def bind_system_startapp"):MENU.index("async def queue_system_channel_connected")]
    assert "paid_bot_conversion_key" in start
    assert '"internal_event": "bot_started"' in start
    assert "yclid" not in section


def test_real_bot_start_is_the_only_bot_conversion_entrypoint():
    start = MENU[MENU.index("async def bind_system_startapp"):MENU.index("async def queue_system_channel_connected")]
    assert 'visit.status = "BOT_STARTED"' in start
    assert 'BotConversion(' in start
    assert 'BotTrafficVisit.status.in_(("HANDOFF_CONFIRMED", "BOT_STARTED"))' in start
    assert 'not identity_link or not visit.max_user_id or visit.max_user_id != str(max_user_id)' in start


def test_bot_start_does_not_fabricate_channel_identity_link():
    start = MENU[MENU.index("async def bind_system_startapp"):MENU.index("async def queue_system_channel_connected")]
    assert "MaxIdentityLink(" not in start
    assert "BotMaxIdentityLink" in start
    assert "BotMaxIdentityLink.bot_visit_id == visit.id" in start
    assert "BotMaxIdentityLink.max_user_id == str(max_user_id)" in start


def test_mini_app_resolves_bot_without_touching_channel_open_branch():
    resolve = MINI[MINI.index("async function resolveChannel"):MINI.index("resolveChannel().catch")]
    assert "data.destination_type === 'BOT'" in resolve
    assert "openBot(data.target_url)" in resolve
    channel = MINI[MINI.index("function openChannel"):MINI.index("async function resolveChannel")]
    assert "window.location.replace(targetUrl)" in channel
    assert "webApp.openMaxLink(targetUrl)" in channel


def test_bot_post_resolve_copies_platform_open_without_closing_miniapp():
    bot = MINI[MINI.index("function openBot"):MINI.index("function reportMaxOpened")]
    assert "miniTelemetry('BOT_HANDOFF_STARTED')" in bot
    assert "platform === 'android'" in bot
    assert "window.location.replace(targetUrl)" in bot
    assert "!isAppleMobile && typeof webApp?.openMaxLink === 'function'" in bot
    assert "webApp.openMaxLink(targetUrl)" in bot
    assert "openViaAnchor(targetUrl)" in bot
    assert "webApp.close()" not in bot
    assert "MINIAPP_CHANNEL_HANDOFF_STARTED" not in bot


def test_bot_handoff_started_is_verified_diagnostic_telemetry_only():
    bot = MINI[MINI.index("function openBot"):MINI.index("function reportMaxOpened")]
    endpoint = MAIN[MAIN.index("async def traffic_miniapp_telemetry"):MAIN.index("async def traffic_channel_open")]
    assert "miniTelemetry('BOT_HANDOFF_STARTED')" in bot
    assert 'payload.event_type == "BOT_HANDOFF_STARTED"' in endpoint
    assert 'await _bot_handoff_visit_from_payload(payload, x_max_init_data, db)' in endpoint
    assert 'await _bot_telemetry_event(' in endpoint
    assert '"BOT_HANDOFF_STARTED"' in MAIN
    assert 'BotConversion' not in endpoint


def test_bot_handoff_started_dedupes_by_exact_visit_and_keeps_channel_branch_separate():
    endpoint = MAIN[MAIN.index("async def traffic_miniapp_telemetry"):MAIN.index("async def traffic_channel_open")]
    assert 'dedupe = f"bot-telemetry:{visit.id}:{event_type}"' in MAIN
    assert 'if payload.event_type == "BOT_HANDOFF_STARTED":' in endpoint
    assert 'visit, trusted = await _telemetry_visit_from_payload(payload, x_max_init_data, db)' in endpoint
    assert 'BotTrafficEvent' in MAIN


def test_required_safe_handoff_event_names_are_present_without_raw_identity_logging():
    for event in (
        "SYSTEM_LANDING_RENDERED", "SYSTEM_REDIRECT_ATTEMPTED", "MAX_STARTAPP_HANDOFF",
        "MINI_APP_LOADED", "HANDOFF_CONFIRMED", "TOKEN_RESOLVED", "MAX_USER_BOUND",
        "SYSTEM_BOT_HANDOFF_CONFIRMED", "BOT_STARTED_CONVERSION_QUEUED",
        "BOT_HANDOFF_STARTED",
    ):
        assert event in MAIN
    assert "raw initData" not in MAIN.lower()


def test_bot_handoff_started_does_not_claim_bot_open_confirmation():
    observability = (ROOT / "app/traffic/bot_start_observability.py").read_text(encoding="utf-8")
    assert '"bot_open_confirmed": UNPROVEN' in observability
    assert '"start_screen_shown": UNPROVEN' in observability
    assert "BOT_OPEN_CONFIRMED" not in MAIN


def test_bot_landing_uses_official_https_max_startapp_contract_only():
    start = MAIN.index('@app.get("/system/{public_id}"')
    end = MAIN.index('@app.get("/health/live")', start)
    section = MAIN[start:end]
    assert "build_native_miniapp_deep_link if _is_mobile_request(request) else build_web_miniapp_deep_link" in section
    assert "build_miniapp_deep_link" not in section
    assert "build_bot_deep_link" not in section


def test_bot_landing_is_operationally_separate_from_channel_redirect():
    start = MAIN.index('@app.get("/system/{public_id}"')
    end = MAIN.index('@app.get("/health/live")', start)
    section = MAIN[start:end]
    assert "_traffic_redirect" not in section
    assert "BotTrafficVisit" in section
    assert "select(TrafficVisit)" not in section
    assert "channel_id" not in section
    assert "user_added" not in section
    assert "JOIN_REQUEST" not in section
