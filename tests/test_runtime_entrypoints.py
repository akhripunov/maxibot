from app.bot.runtime import MaxBotRuntime
from app.integrations.max_client import MaxMessage, MaxUpdate, RealMaxClient


def test_manual_start_and_deep_link_are_distinct_entrypoints():
    manual = MaxUpdate("message_created", user_id="u1", raw={}, message=MaxMessage("m", "u1", "User", "/start"))
    deep_link = MaxUpdate("bot_started", user_id="u1", raw={"payload": "peterburg-24"})
    assert manual.message.text.strip().lower() == "/start"
    assert MaxBotRuntime._start_payload(deep_link) == "peterburg-24"


def test_real_message_link_recovers_forwarded_source_channel():
    client = RealMaxClient(token="unit-token", base_url="https://platform-api2.max.ru")
    update = client._parse_update({
        "update_type": "message_created",
        "user": {"user_id": 7, "name": "Администратор"},
        "message": {
            "mid": "m-forwarded",
            "sender": {"user_id": 7, "name": "Администратор"},
            "recipient": {"user_id": 99},
            "link": {"type": "forward", "recipient": {"chat_id": 12345}},
            "body": {"text": "пост"},
        },
    })
    assert update.message.channel_id == "12345"
    assert MaxBotRuntime._forwarded_channel_id(update.message) == "12345"


def test_start_payload_does_not_accept_local_query_string():
    update = MaxUpdate("bot_started", user_id="u1", raw={"start": "/start?channel=abc"})
    assert MaxBotRuntime._start_payload(update) is None


def test_traffic_start_uses_one_neutral_channel_cta_without_warning_copy():
    from pathlib import Path

    root = Path(__file__).parents[1]
    source = (root / "app" / "bot" / "runtime.py").read_text()
    assert 'f"Нажмите кнопку ниже, чтобы открыть канал «{traffic[\'channel_title\']}»."' in source
    assert '"Открыть канал"' in source
    assert "Этот канал сейчас не принимает обращения через MAXIBOT" not in "\n".join(
        path.read_text() for path in (root / "app").rglob("*.py")
    )
    assert "заявка будет проверена автоматически" not in source


def test_duplicate_logical_start_is_claimed_once():
    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime._recent_start_keys = {}
    assert runtime._claim_start("user-1", "opaque-token") is True
    assert runtime._claim_start("user-1", "opaque-token") is False


def test_normal_start_replaces_old_service_panel_with_fresh_bottom_copy():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    assert 'message_id = await self._render(user_id, "home", force_new=True)' in source


def test_normal_start_preserves_id_until_atomic_panel_replacement():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    start_section = source[source.index("    async def _start("):source.index("    async def _callback(")]
    assert "await self._close_messages_ui(user_id)" not in start_section
    assert "reset_navigation(user_id, preserve_service_message=True)" in start_section


def test_stale_promotion_navigation_is_not_discarded():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    assert 'navigation_action = action == "promotion" or action.startswith(("promotion_", "channels", "channel:"))' in source
    assert "and not navigation_action" in source


def test_dynamic_channel_lists_do_not_create_replacement_messages():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    assert "ADMIN_UI_REPLACE_LIST" not in source


def test_stale_channel_and_landing_callbacks_recover_to_current_lists():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    assert 'if action.startswith("channel:")' in source
    assert 'if not await self.repo.channel_owned(user_id, stale_channel_id):' in source
    assert 'await self._render(user_id, "channels")' in source
    assert 'await self._render(user_id, "promotion")' in source


def test_callback_navigation_uses_clicked_message_replacement_body():
    body = MaxBotRuntime._callback_replacement_body(
        "Мои каналы",
        [[{"text": "Москва 365", "callback_data": "channel:one"}, {"text": "Твой Петербург", "callback_data": "channel:two"}]],
        [],
    )
    assert body["text"] == "Мои каналы"
    buttons = body["attachments"][0]["payload"]["buttons"][0]
    assert [button["payload"] for button in buttons] == ["channel:one", "channel:two"]


def test_navigation_resolves_legacy_channel_references_with_owner_scope():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    menu_source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    assert "resolve_owned_channel_id" in source
    assert "Channel.external_id == str(reference)" in menu_source
    assert "Channel.public_slug == str(reference)" in menu_source
    assert "Admin.external_id == str(external_id)" in menu_source


def test_stale_navigation_never_renders_channel_not_found_message():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    assert 'return await render_screen(db, admin, "channels")' in source
    assert 'return await render_screen(db, admin, "promotion")' in source
    assert "Канал не найден" not in source
