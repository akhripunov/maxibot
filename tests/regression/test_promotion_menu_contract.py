from pathlib import Path


MENU_SOURCE = (Path(__file__).resolve().parents[2] / "app/bot/menu.py").read_text()


def test_promotion_channel_menu_has_short_yandex_label():
    assert 'yandex_button = "🔗 Яндекс.Директ ✅" if yandex else "🔗 Яндекс.Директ ⚠️ не подключен"' in MENU_SOURCE
    assert '(yandex_button, f"promotion_yandex:{channel.id}")' in MENU_SOURCE
    assert "Подключить Яндекс.Метрику" not in MENU_SOURCE


def test_promotion_channel_menu_does_not_expose_max_target_check():
    assert "Проверить MAX target" not in MENU_SOURCE


def test_promotion_channel_menu_keeps_expected_navigation_items():
    for label in ("📄 Лендинг", "📊 Аналитика", "⚙️ Настройки", "⬅️ Назад"):
        assert label in MENU_SOURCE


def test_yandex_screen_does_not_expose_server_configuration_to_users():
    yandex_screen = MENU_SOURCE[MENU_SOURCE.index('if screen.startswith("promotion_yandex:")'):MENU_SOURCE.index('if screen.startswith("promotion_settings:")')]
    for forbidden in ("YANDEX_OAUTH_CLIENT_ID", "YANDEX_OAUTH_CLIENT_SECRET", "YANDEX_OAUTH_REDIRECT_URI", "Callback:", "Проверить готовность OAuth"):
        assert forbidden not in yandex_screen
    assert "Авторизоваться в Яндекс" in yandex_screen


def test_yandex_screen_offers_reconnect_after_a_real_connection():
    yandex_screen = MENU_SOURCE[MENU_SOURCE.index('if screen.startswith("promotion_yandex:")'):MENU_SOURCE.index('if screen.startswith("promotion_settings:")')]
    assert "Авторизоваться в Яндекс" in yandex_screen
    assert "if oauth_configured:" in yandex_screen


def test_yandex_direct_oauth_is_the_channel_menu_flow():
    yandex_screen = MENU_SOURCE[MENU_SOURCE.index('if screen.startswith("promotion_yandex:")'):MENU_SOURCE.index('if screen.startswith("promotion_settings:")')]
    assert "🔐 Подключить Яндекс Директ" not in yandex_screen
    assert 'yandex_direct_oauth_url(admin.external_id, selected_id)' in yandex_screen
    assert 'Авторизоваться в Яндекс.Директ' in yandex_screen
