from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MENU = (ROOT / "app" / "bot" / "menu.py").read_text()
RUNTIME = (ROOT / "app" / "bot" / "runtime.py").read_text()
MODELS = (ROOT / "app" / "db" / "models.py").read_text()
MIGRATION = (ROOT / "alembic" / "versions" / "0045_admin_onboarding_seen.py").read_text()


def test_onboarding_is_persisted_and_existing_admins_are_preserved():
    assert "onboarding_seen" in MODELS
    assert "UPDATE admins SET onboarding_seen = TRUE" in MIGRATION
    assert "onboarding_seen" in MIGRATION


def test_first_start_uses_onboarding_and_buttons_use_existing_routes():
    assert 'first_screen = "home" if await self.repo.onboarding_seen' in RUNTIME
    assert '"onboarding"' in RUNTIME
    assert 'action in {"connect_channel", "about"}' in RUNTIME
    assert 'await self.repo.mark_onboarding_seen' in RUNTIME
    assert 'await self.repo.reset_onboarding' in RUNTIME
    assert 'buttons(("📢 Подключить канал", "connect_channel"), ("💎 О сервисе", "about"))' in MENU


def test_onboarding_screen_is_text_only():
    assert 'media = []' in MENU
    assert 'screen == "about"' in MENU
    assert 'screen == "connect_channel"' in MENU


def test_bots_overview_is_a_direct_alias_to_my_bots():
    assert 'return await render_screen(db, admin, "promotion")' in MENU
    assert 'await self._render(user_id, "promotion")' in RUNTIME


def test_connected_bots_have_no_separate_list_screen():
    assert 'Connected bots are shown only in' in MENU
    assert '("🤖 Мои боты", "my_bots")' not in MENU


def test_channel_and_bot_yandex_buttons_show_connection_status():
    assert '"🔗 Яндекс.Директ ✅" if yandex else "🔗 Яндекс.Директ ❌"' in MENU
    assert '"🔗 Яндекс.Директ ✅" if bot_yandex_connected else "🔗 Яндекс.Директ ❌"' in MENU


def test_add_bot_shows_intro_before_instruction():
    assert 'await self._render(user_id, "connect_bot_intro")' in RUNTIME
    assert 'if screen == "connect_bot_intro":' in MENU
    assert 'buttons(("✅ Продолжить подключение", "custom_bot_token")' in MENU
    assert 'if screen == "connect_bot":' in MENU


def test_admin_command_uses_platform_owner_allowlist():
    assert 'async def is_platform_admin' in MENU
    assert 'await self.repo.is_platform_admin(target_id)' in RUNTIME
    assert 'if not target_id or not await self.repo.is_admin(target_id):' not in RUNTIME


def test_channel_promotion_menu_keeps_only_yandex_analytics_and_back():
    start = MENU.index('if screen.startswith("promotion_channel:")')
    end = MENU.index('if screen.startswith("promotion_landing:")', start)
    block = MENU[start:end]
    assert 'yandex_button' in block
    assert '"📊 Аналитика"' in block
    assert '"⬅️ Назад"' in block
    assert '"📄 Лендинг"' not in block
    assert '"⚙️ Настройки"' not in block


def test_bot_promotion_menu_keeps_only_yandex_analytics_and_back():
    start = MENU.index('if screen.startswith("promotion_bot:")')
    end = MENU.index('if screen.startswith(("promotion_bot_landing:"', start)
    block = MENU[start:end]
    assert 'yandex_button' in block
    assert '"📊 Аналитика"' in block
    assert '"✏️ Тексты"' in block
    assert '"📣 Рассылка"' in block
    assert '"🔑 Обновить токен"' in block
    assert '"⛔ Отключить бота"' in block
    assert '"⬅️ Назад"' in block
    assert '"📄 Лендинг"' not in block
    assert '"⚙️ Настройки"' not in block


def test_system_promotion_menu_keeps_only_yandex_analytics_and_back():
    start = MENU.index('if screen == "promotion_maxibot":')
    end = MENU.index('if screen in {"promotion_maxibot_landing",', start)
    block = MENU[start:end]
    assert '"⚡ Яндекс"' in block
    assert '"📊 Аналитика"' in block
    assert '"⬅️ Назад"' in block
    assert '"📄 Лендинг"' not in block
    assert '"⚙️ Настройки"' not in block


def test_channel_connected_message_is_single_line():
    assert 'f"✅ Канал «{result.get(\'title\') or \'MAX\'}» подключён к MAXIBOT."' in RUNTIME
    assert 'Он уже доступен в разделах «Мои каналы» и «Продвижение».' not in RUNTIME


def test_private_bot_removal_resets_onboarding_but_channel_removal_does_not():
    assert 'str(update.user_id) == str(update.chat_id)' in RUNTIME
    assert '"dialog_removed", "bot_stopped"' in RUNTIME
    assert 'private_dialog_event = update.update_type in {"dialog_removed", "bot_stopped"}' in RUNTIME
    assert 'MAX_PRIVATE_DIALOG_REMOVED_ONBOARDING_RESET' in RUNTIME
