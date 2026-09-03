from pathlib import Path


MENU = Path("app/bot/menu.py").read_text()
RUNTIME = Path("app/bot/runtime.py").read_text()
MAIN = Path("app/main.py").read_text()
OAUTH = Path("app/integrations/yandex_oauth.py").read_text()
METRICA = Path("app/integrations/yandex_metrica.py").read_text()


def test_system_maxibot_is_an_owner_only_row_in_promotion_list():
    assert "def is_maxibot_owner(admin: Admin)" in MENU
    assert 'if not channels and not is_maxibot_owner(admin):' in MENU
    assert 'rows.append([("🤖 МАКСИБОТ", "promotion_maxibot")])' in MENU
    assert 'owner_id = (settings.maxibot_owner_user_id or "").strip()' in MENU


def test_system_maxibot_screen_is_owner_gated_without_public_channel_shortcut():
    start = MENU.index('if screen == "promotion_maxibot":')
    end = MENU.index('if screen.startswith("promotion_channel:"):', start)
    screen = MENU[start:end]
    assert 'if not is_maxibot_owner(admin):' in screen
    assert 'f"📣 Продвижение\\n\\nКанал: МАКСИБОТ\\n\\n"' in screen
    assert 'system_yandex' in screen
    assert 'system_counter' in screen
    assert 'system_landing' in screen
    assert '"🌐 MAX-канал"' not in screen
    assert '"promotion"' in screen
    assert '("⬅️ Назад", "promotion")' in screen


def test_system_maxibot_callback_is_first_class_navigation():
    assert 'action == "promotion_maxibot"' in RUNTIME
    assert '"promotion_maxibot"' in RUNTIME


def test_system_maxibot_has_separate_channel_like_sections():
    for action in ("promotion_maxibot_landing", "promotion_maxibot_yandex", "promotion_maxibot_analytics", "promotion_maxibot_settings"):
        assert action in MENU
        assert action in RUNTIME
    assert "SystemPromotionProfile" in MENU
    system_screen = MENU[MENU.index('if screen == "promotion_maxibot":'):MENU.index('if screen.startswith("promotion_channel:"):', MENU.index('if screen == "promotion_maxibot":'))]
    assert "select(SystemPromotionProfile)" in system_screen
    assert "select(TrafficProfile)" not in system_screen


def test_system_yandex_uses_separate_oauth_and_bot_started_goal():
    assert '"/oauth/yandex/system/start"' in MAIN
    assert 'flow.get("system") == "maxibot"' in MAIN
    assert 'save_system_token(flow["admin"], payload' in MAIN
    assert "ensure_system_setup(encrypted)" in MAIN
    assert "create_system_state" in OAUTH
    assert "SystemPromotionProfile" in OAUTH
    assert 'goal_name = "bot_started"' in METRICA


def test_system_start_does_not_enter_channel_flow():
    runtime_start = RUNTIME[RUNTIME.index("bind_system_startapp"):RUNTIME.index("bind_traffic_startapp")]
    assert 'await self.repo.reset_navigation(user_id, preserve_service_message=True)' in runtime_start
    assert 'await self._render(user_id, "home", force_new=True)' in runtime_start
    assert "SYSTEM_START_MENU_RENDERED" in runtime_start
    assert "Вы запустили MAXIBOT" not in runtime_start
    assert "channel_id" not in runtime_start
    assert "SystemConversion" not in runtime_start


def test_system_landing_starts_the_bot_not_the_channel_miniapp():
    start = MAIN.index('@app.get("/system/{public_id}"')
    end = MAIN.index('@app.get("/health/live")', start)
    system_landing = MAIN[start:end]
    assert "build_web_bot_deep_link" in system_landing
    assert "build_bot_deep_link" in system_landing
    assert "build_native_miniapp_deep_link" not in system_landing
    assert "build_web_miniapp_deep_link" not in system_landing


def test_system_landing_uses_its_own_metrika_counter_before_bot_handoff():
    start = MAIN.index('@app.get("/system/{public_id}"')
    end = MAIN.index('@app.get("/health/live")', start)
    system_landing = MAIN[start:end]
    assert "valid_counter_id(profile.yandex_counter_id)" in system_landing
    assert "metrica_tag_html(metrica_counter)" in system_landing
    assert "ym(" in system_landing
    assert "build_native_miniapp_deep_link" not in system_landing


def test_system_landing_captures_attribution_metadata_and_client_id_safely():
    start = MAIN.index('@app.get("/system/{public_id}"')
    end = MAIN.index('@app.post("/system/traffic/metrica/client-id")', start)
    system_landing = MAIN[start:end]
    for field in ("utm_content", "utm_term", "referer", "user_agent"):
        assert f"{field}=request." in system_landing
    assert "maxibot-metrica-client-id" in system_landing
    assert "system/traffic/metrica/client-id" in system_landing
    assert "build_web_bot_deep_link" in system_landing
    assert "build_bot_deep_link" in system_landing


def test_system_client_id_endpoint_is_separate_from_channel_traffic_endpoint():
    assert '@app.post("/system/traffic/metrica/client-id")' in MAIN
    endpoint = MAIN[MAIN.index('@app.post("/system/traffic/metrica/client-id")'):MAIN.index('@app.get("/health/live")')]
    assert "SystemTrafficVisit" in endpoint
    assert "select(TrafficVisit)" not in endpoint


def test_system_yandex_screen_has_only_oauth_and_promotion_back_buttons():
    start = MENU.index('elif screen == "promotion_maxibot_yandex":')
    end = MENU.index('elif screen == "promotion_maxibot_analytics":', start)
    screen = MENU[start:end]
    assert 'oauth_label = "🔄 Переподключить Яндекс" if status else "🔐 Подключить Яндекс"' in screen
    assert '"⬅️ Назад", "callback_data": "promotion_maxibot"' in screen
    assert '"⬅️ Продвижение"' not in screen
    assert '"⬅️ МАКСИБОТ"' not in screen


def test_system_promotion_displays_yandex_owner_login_without_cross_channel_lookup():
    start = MENU.index('if screen == "promotion_maxibot":')
    end = MENU.index('if screen in {"promotion_maxibot_landing"', start)
    screen = MENU[start:end]
    assert "profile.yandex_owner_login" in screen
    assert "TrafficProfile" not in screen


def test_system_channel_connected_goal_is_provisioned_without_replacing_bot_started():
    assert 'goal_name = "bot_started"' in METRICA
    assert 'channel_goal_name = "maxibot_channel_connected"' in METRICA
    assert 'ensure_action_goal(counter_id, channel_goal_name)' in METRICA


def test_system_conversion_allows_one_visit_per_goal_and_keeps_linkage_fail_closed():
    models = Path("app/db/models.py").read_text()
    migration = Path("alembic/versions/0041_system_conversion_per_goal.py").read_text()
    assert 'UniqueConstraint("visit_id", "goal"' in models
    assert "uq_system_conversion_visit_goal" in migration
    assert "blocked_no_system_visit" in MENU
    assert 'goal="maxibot_channel_connected"' in MENU


def test_official_channel_connect_queues_only_the_system_bot_branch_goal():
    assert "queue_system_channel_connected" in MENU
    assert "SYSTEM_CHANNEL_CONNECTED_CONVERSION" in RUNTIME
    assert "result.get(\"status\") in {\"created\", \"updated\", \"duplicate\"}" in RUNTIME


def test_connected_bots_are_listed_in_promotion_without_reusing_channel_rows():
    promotion = MENU[MENU.index('if screen == "promotion":'):MENU.index('if screen == "promotion_connect":')]
    assert "select(BotConnection)" in promotion
    assert 'f"promotion_bot:{bot.id}"' in promotion
    assert 'f"🤖 {bot.display_name or bot.username}"' in promotion
    assert 'f"promotion_channel:{channel.id}"' in promotion


def test_bot_promotion_is_owner_scoped_and_keeps_system_and_channel_scopes_separate():
    start = MENU.index('if screen.startswith("promotion_bot:"):')
    end = MENU.index('if screen.startswith("promotion_channel:"):', start)
    screen = MENU[start:end]
    assert 'BotConnection.id == bot_id' in screen
    assert 'BotConnection.owner_admin_id == admin.id' in screen
    assert "bot-scoped" in screen
    assert "TrafficProfile" not in screen
    assert "SystemPromotionProfile" not in screen


def test_bot_promotion_callbacks_are_owner_checked_before_rendering():
    assert 'if action.startswith("promotion_bot")' in RUNTIME
    assert 'await self.repo.bot_owned(user_id, bot_id)' in RUNTIME
    assert 'await self._render(user_id, action)' in RUNTIME
