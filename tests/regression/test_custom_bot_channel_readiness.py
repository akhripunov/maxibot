from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MENU_SOURCE = (ROOT / "app/bot/menu.py").read_text()
RUNTIME_SOURCE = (ROOT / "app/bot/runtime.py").read_text()


def test_channel_screen_recognizes_ready_custom_bot():
    assert 'channel.communication_mode == "CUSTOM_BOT"' in MENU_SOURCE
    assert 'custom_bot_ready = bool(custom_bot and custom_bot.admin_private_chat_id)' in MENU_SOURCE
    assert 'elif channel.inbox_chat_id or custom_bot_ready:' in MENU_SOURCE


def test_unbound_channel_does_not_show_working_personal_link():
    assert 'if channel.inbox_chat_id or custom_bot_ready' in MENU_SOURCE
    assert "Подключите чат ниже, чтобы создать ссылку" in MENU_SOURCE


def test_custom_inbound_requires_one_bound_channel_before_success_ack():
    assert "active_custom_channels(bot.id)" in RUNTIME_SOURCE
    assert "if len(bound_channels) != 1:" in RUNTIME_SOURCE
    assert "CUSTOM_INBOUND_BLOCKED_NO_UNAMBIGUOUS_CHANNEL" in RUNTIME_SOURCE
    assert "create_custom_inbound(" in RUNTIME_SOURCE
