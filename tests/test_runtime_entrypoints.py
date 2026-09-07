from app.bot.runtime import MaxBotRuntime
from app.integrations.max_client import MaxMessage, MaxUpdate, RealMaxClient
import pytest


def test_manual_start_and_deep_link_are_distinct_entrypoints():
    manual = MaxUpdate("message_created", user_id="u1", raw={}, message=MaxMessage("m", "u1", "User", "/start"))
    deep_link = MaxUpdate("bot_started", user_id="u1", raw={"payload": "peterburg-24"})
    assert manual.message.text.strip().lower() == "/start"
    assert MaxBotRuntime._start_payload(deep_link) == "peterburg-24"


def test_admin_command_is_distinct_from_start_payload_and_supports_bot_suffix():
    assert MaxBotRuntime._is_admin_command("/admin") is True
    assert MaxBotRuntime._is_admin_command("/admin@maxibot") is True
    assert MaxBotRuntime._is_admin_command("/admin opaque-payload") is True
    assert MaxBotRuntime._is_admin_command("/start opaque-payload") is False
    assert MaxBotRuntime._is_admin_command("admin") is False


def test_admin_command_routes_through_database_admin_check_and_denies_non_admin():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    section = source[source.index("    async def _message("):source.index("    async def _deliver_inbox_message(")]
    assert "if self._is_admin_command(normalized_text):" in section
    assert "await self.repo.is_platform_admin(target_id)" in section
    assert "Раздел администратора доступен только администраторам." in section
    assert 'await self._render(target_id, "owner_admin")' in section
    assert "OWNER_ADMIN_NOT_CONFIGURED" not in section


class _AdminCommandRepo:
    def __init__(self, admin: bool):
        self.admin = admin
        self.reset = []
        self.cleared = []

    async def is_platform_admin(self, external_id):
        return self.admin

    async def reset_navigation(self, external_id):
        self.reset.append(external_id)

    async def clear_support_input(self, external_id):
        self.cleared.append(external_id)


class _AdminCommandClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, target, text, *args, **kwargs):
        self.sent.append((target, text))
        return "message-1"


@pytest.mark.asyncio
async def test_admin_command_admin_user_opens_owner_menu():
    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime.repo = _AdminCommandRepo(admin=True)
    runtime.client = _AdminCommandClient()
    rendered = []

    async def render(target, screen, *args, **kwargs):
        rendered.append((target, screen))

    runtime._render = render
    update = MaxUpdate(
        "message_created", user_id="admin-1",
        message=MaxMessage("m-admin", "admin-1", "Admin", "/admin"),
    )
    await runtime._message(update)
    assert rendered == [("admin-1", "owner_admin")]
    assert runtime.client.sent == []


@pytest.mark.asyncio
async def test_admin_command_non_admin_gets_explicit_denial():
    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime.repo = _AdminCommandRepo(admin=False)
    runtime.client = _AdminCommandClient()
    runtime._render = None
    update = MaxUpdate(
        "message_created", user_id="user-1",
        message=MaxMessage("m-user", "user-1", "User", "/admin"),
    )
    await runtime._message(update)
    assert runtime.client.sent == [
        ("user-1", "⛔ Раздел администратора доступен только администраторам.")
    ]


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


def test_traffic_start_binds_silently_without_channel_cta():
    from pathlib import Path

    root = Path(__file__).parents[1]
    source = (root / "app" / "bot" / "runtime.py").read_text()
    assert "TRAFFIC_STARTAPP_BOUND_SILENT" in source
    assert '"Открыть канал"' not in source
    assert "Этот канал сейчас не принимает обращения через MAXIBOT" not in "\n".join(
        path.read_text() for path in (root / "app").rglob("*.py")
    )
    assert "заявка будет проверена автоматически" not in source


def test_duplicate_logical_start_is_claimed_once():
    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime._recent_start_keys = {}
    runtime._recent_start_users = {}
    assert runtime._claim_start("user-1", "opaque-token") is True
    assert runtime._claim_start("user-1", "opaque-token") is False


def test_bot_started_and_message_start_are_one_logical_launch():
    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime._recent_start_keys = {}
    runtime._recent_start_users = {}

    assert runtime._claim_start("user-1", "opaque-token") is True
    assert runtime._claim_start("user-1", None) is False


def test_normal_start_replaces_old_service_panel_with_fresh_bottom_copy():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    assert 'message_id = await self._render(user_id, first_screen, force_new=True)' in source


def test_normal_start_preserves_id_until_atomic_panel_replacement():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    start_section = source[source.index("    async def _start("):source.index("    async def _callback(")]
    assert "await self._close_messages_ui(user_id)" not in start_section
    assert "reset_navigation(user_id, preserve_service_message=True)" in start_section


def test_stale_promotion_navigation_is_not_discarded():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    assert "navigation_action = action in {" in source
    assert '"promotion_maxibot"' in source
    assert "and not navigation_action" in source


def test_message_settings_navigation_is_not_discarded_as_stale_callback():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    assert 'action.startswith(("channel:", "settings:"' in source
    assert '"promotion_channel:"' in source
    assert 'return ("⚙️ Настройка сообщений пользователя' in (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()


def test_channel_overview_back_returns_to_current_promotion_channel():
    from pathlib import Path

    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    section = menu[menu.index('if screen.startswith("channel:") or screen == "channel"'):menu.index('if screen.startswith("inbox_connect:")')]
    assert '("⚙️ Настройка сообщений пользователя", f"settings:{channel.id}")' in section
    assert '("⬅️ Назад", f"promotion_channel:{channel.id}")' in section
    assert '("⬅️ Назад", "channels")' not in section


def test_channel_messages_back_returns_to_same_parent_channel():
    from pathlib import Path

    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    section = menu[menu.index('if screen.startswith("messages:")'):menu.index('if screen.startswith("conversation:")')]
    assert '("⬅️ Назад", f"channel:{channel.id}")' in section


def test_stale_channel_guard_remains_required_for_channel_navigation():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    assert 'if action.startswith("channel:")' in source
    assert 'if not await self.repo.channel_owned(user_id, stale_channel_id):' in source


def test_delete_cancel_returns_to_current_promotion_channel():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    section = source[source.index('elif action.startswith("delete:")'):source.index('elif action == "read_all"')]
    assert 'await self._render(user_id, f"promotion_channel:{channel_id}", channel_id)' in section
    assert 'await self._render(user_id, f"channel:{channel_id}", channel_id)' not in section


def test_daily_reports_toggle_is_rendered_between_analytics_and_inbox():
    from pathlib import Path

    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    start = menu.index('if screen.startswith("promotion_channel:")')
    end = menu.index('if screen.startswith("promotion_landing:")', start)
    section = menu[start:end]
    assert 'Ежедневные отчёты:' in menu
    assert 'daily_reports:{channel.id}' in section
    assert section.index('("📊 Аналитика"') < section.index('daily_reports:{channel.id}') < section.index('("📩 Обращения / Предложка"')


def test_daily_reports_callback_updates_channel_and_returns_to_promotion_channel():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    section = source[source.index('elif action.startswith("daily_reports:")'):source.index('elif action == "messages:global"')]
    assert 'await self.repo.set_daily_reports(channel_id, enabled)' in section
    assert 'await self._render(user_id, f"promotion_channel:{channel_id}", channel_id)' in section


def test_about_screen_labels_home_navigation_explicitly():
    from pathlib import Path

    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    section = menu[menu.index('if screen == "about":'):menu.index('if screen == "owner_admin":')]
    assert '{"text": "⬅️ Главное меню", "callback_data": "home"}' in section


def test_service_subscription_is_in_home_and_not_channel_menu():
    from pathlib import Path

    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    home = menu[menu.index('return menu_text(len(channels)'):menu.index('if screen == "ads_storage"')]
    assert '("📚 Мои каналы", "promotion")' in home
    assert '("💳 Оформить подписку", "subscription")' in home
    assert home.index('("📚 Мои каналы", "promotion")') < home.index('("💳 Оформить подписку", "subscription")') < home.index('("ℹ️ О сервисе", "about")')

    channel = menu[menu.index('if screen.startswith("promotion_channel:")'):menu.index('if screen.startswith("promotion_landing:")')]
    assert '"💳 Оформить подписку"' not in channel
    assert '"🗑 Удалить канал"' in channel


def test_service_subscription_order_has_no_channel_dependency():
    from pathlib import Path

    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    runtime = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    model = (Path(__file__).parents[1] / "app" / "db" / "models.py").read_text()
    assert 'f"subscription_plan:service:{months}"' in menu
    assert 'service_subscription = selected_id == "service"' in runtime
    assert 'channel_id=channel.id if channel else None' in runtime
    assert 'Mapped[Optional[str]]' in model


def test_navigation_callback_returns_the_rendered_screen_to_active_max_client():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    section = source[source.index("    async def _navigation_callback("):source.index("    async def _ack_callback_in_background(")]
    assert "replacement = self._callback_replacement_body(text, keyboard, media)" in section
    assert "await self.client.answer_callback(update.callback_id, replacement)" in section
    assert "await self.client.delete_message" not in section
    assert "await self.client.edit_message" not in section
    assert 'if screen.startswith("settings:"):' in section
    assert "MAX_SETTINGS_PANEL_CREATED" in section


def test_message_settings_does_not_send_an_unuploaded_image_url():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    assert 'payload": {"url": settings.max_public_url' not in source


@pytest.mark.asyncio
async def test_settings_callback_resolves_owned_channel_and_renders_settings_screen():
    class Repo:
        async def resolve_owned_channel_id(self, user_id, reference):
            assert (user_id, reference) == ("admin-1", "legacy-channel")
            return "channel-1"

        async def render(self, user_id, screen, channel_id=None):
            return "settings", [], "service-1", []

        async def service_message_id(self, user_id):
            return "service-1"

        async def save_service_message(self, user_id, message_id):
            assert (user_id, message_id) == ("admin-1", "service-2")

    class Client:
        def __init__(self):
            self.edited = []
            self.answered = []
            self.sent = []

        async def edit_message(self, target, message_id, text, keyboard, media=None):
            self.edited.append((target, message_id, text, keyboard, media))

        async def answer_callback(self, callback_id, message=None):
            self.answered.append(callback_id)

        async def send_message(self, target, text, keyboard=None, media=None):
            self.sent.append((target, text, keyboard, media))
            return "service-2"

    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime.repo = Repo()
    runtime.client = Client()
    runtime._recent_callback_ids = {}
    update = MaxUpdate(
        "message_callback", chat_id="admin-1", user_id="admin-1",
        callback_id="cb-settings", button_payload="settings:legacy-channel",
        message=MaxMessage("service-1", "admin-1", "Admin", "old"),
    )

    await runtime._callback(update)

    assert runtime.client.answered == ["cb-settings"]
    assert runtime.client.edited == []
    assert runtime.client.sent[0][0:2] == ("admin-1", "settings")


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
