from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import menu
from app.db.models import Admin, Base, BotConnection, Channel
from app.bot.runtime import MaxBotRuntime
from app.integrations.max_client import MaxUpdate


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


def pairs(keyboard):
    return [(button["text"], button["callback_data"]) for row in keyboard for button in row]


def callback_values(keyboard):
    return [button["callback_data"] for row in keyboard for button in row if "callback_data" in button]


async def add_bot(db, owner, *, bot_id, name, username):
    bot = BotConnection(
        id=bot_id,
        owner_admin_id=owner.id,
        owner_max_user_id=owner.external_id,
        max_bot_id=f"max-{bot_id}",
        username=username,
        display_name=name,
        encrypted_token="encrypted-test-token",
    )
    db.add(bot)
    await db.flush()
    return bot


@pytest.mark.asyncio
async def test_home_offers_my_bots_immediately_after_my_channels(db):
    user = Admin(external_id="user")
    db.add(user)
    await db.flush()

    _, keyboard = await menu.render_screen(db, user, "home")

    assert pairs(keyboard) == [
        ("📢 Подключить канал", "connect_channel"),
        ("📚 Мои каналы", "promotion"),
        ("🤖 Мои боты", "my_bots"),
        ("💳 Моя подписка", "subscription"),
        ("ℹ️ О сервисе", "about"),
    ]


@pytest.mark.asyncio
async def test_my_bots_shows_empty_state_and_existing_add_bot_flow(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "owner")
    user = Admin(external_id="ordinary")
    db.add(user)
    await db.flush()

    text, keyboard = await menu.render_screen(db, user, "my_bots")

    assert text.startswith("🤖 Мои боты")
    assert "пока нет" in text
    assert pairs(keyboard) == [("🤖 Добавить бота", "connect_bot"), ("⬅️ Главное меню", "home")]


@pytest.mark.asyncio
async def test_user_with_only_a_channel_keeps_lists_independent(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "owner")
    user = Admin(external_id="ordinary")
    db.add(user)
    await db.flush()
    channel = Channel(admin_id=user.id, external_id="channel-ext", title="Channel", public_slug="channel")
    db.add(channel)
    await db.flush()

    _, channel_keyboard = await menu.render_screen(db, user, "promotion")
    _, bots_keyboard = await menu.render_screen(db, user, "my_bots")

    assert pairs(channel_keyboard) == [
        ("Channel", f"promotion_channel:{channel.id}"),
        ("📣 Подключить ещё канал", "promotion_connect"),
        ("⬅️ Главное меню", "home"),
    ]
    assert pairs(bots_keyboard) == [
        ("🤖 Добавить бота", "connect_bot"),
        ("⬅️ Главное меню", "home"),
    ]


@pytest.mark.asyncio
async def test_user_with_only_bots_does_not_show_them_as_channels(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "owner")
    user = Admin(external_id="ordinary")
    db.add(user)
    await db.flush()
    bot = await add_bot(db, user, bot_id="only-bot", name="Only bot", username="@onlybot")

    channel_text, channel_keyboard = await menu.render_screen(db, user, "promotion")
    _, bots_keyboard = await menu.render_screen(db, user, "my_bots")

    assert "Подключение канала" in channel_text
    assert pairs(channel_keyboard) == [("⬅️ Назад", "home")]
    assert pairs(bots_keyboard) == [
        ("🟡 Only bot · @onlybot", f"promotion_bot:{bot.id}"),
        ("⚙️ Управление", f"custom_bot:{bot.id}"),
        ("🤖 Добавить бота", "connect_bot"),
        ("⬅️ Главное меню", "home"),
    ]


@pytest.mark.asyncio
async def test_my_bots_lists_all_owned_bots_and_owner_only_system_bot(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "owner")
    owner = Admin(external_id="owner")
    other = Admin(external_id="other")
    db.add_all([owner, other])
    await db.flush()
    own_a = await add_bot(db, owner, bot_id="bot-a", name="Agata", username="@agata")
    own_b = await add_bot(db, owner, bot_id="bot-b", name="Support", username="@support")
    await add_bot(db, other, bot_id="foreign", name="Private", username="@private")

    _, keyboard = await menu.render_screen(db, owner, "my_bots")
    assert pairs(keyboard) == [
        ("🤖 MAXIBOT", "promotion_maxibot"),
        ("🟡 Agata · @agata", f"promotion_bot:{own_a.id}"),
        ("⚙️ Управление", f"custom_bot:{own_a.id}"),
        ("🟡 Support · @support", f"promotion_bot:{own_b.id}"),
        ("⚙️ Управление", f"custom_bot:{own_b.id}"),
        ("🤖 Добавить бота", "connect_bot"),
        ("⬅️ Главное меню", "home"),
    ]
    assert "foreign" not in str(keyboard)


@pytest.mark.asyncio
async def test_owner_keeps_system_bot_when_there_are_no_customer_bots(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "owner")
    owner = Admin(external_id="owner")
    db.add(owner)
    await db.flush()

    text, keyboard = await menu.render_screen(db, owner, "my_bots")

    assert "пока нет подключённых пользовательских ботов" in text.lower()
    assert pairs(keyboard) == [
        ("🤖 MAXIBOT", "promotion_maxibot"),
        ("🤖 Добавить бота", "connect_bot"),
        ("⬅️ Главное меню", "home"),
    ]


@pytest.mark.asyncio
async def test_my_channels_contains_only_owned_channels_and_no_bot_controls(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "owner")
    owner = Admin(external_id="owner")
    db.add(owner)
    await db.flush()
    channel = Channel(admin_id=owner.id, external_id="channel-ext", title="Москва 365", public_slug="moscow")
    db.add(channel)
    await add_bot(db, owner, bot_id="bot-a", name="Agata", username="@agata")
    await db.flush()

    _, keyboard = await menu.render_screen(db, owner, "promotion")

    assert pairs(keyboard) == [
        ("Москва 365", f"promotion_channel:{channel.id}"),
        ("📣 Подключить ещё канал", "promotion_connect"),
        ("⬅️ Главное меню", "home"),
    ]


@pytest.mark.asyncio
async def test_bot_lifecycle_screens_return_to_my_bots(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "owner")
    owner = Admin(external_id="owner")
    db.add(owner)
    await db.flush()
    bot = await add_bot(db, owner, bot_id="owned-bot", name="Support", username="@support")

    _, intro_keyboard = await menu.render_screen(db, owner, "connect_bot_intro")
    _, failure_keyboard = await menu.render_screen(db, owner, "custom_bot_activation_error")
    _, activated_keyboard = await menu.render_screen(db, owner, f"custom_bot_active:{bot.id}")
    _, management_keyboard = await menu.render_screen(db, owner, f"custom_bot:{bot.id}")
    _, refresh_keyboard = await menu.render_screen(db, owner, "custom_bot_refresh_token")

    assert pairs(intro_keyboard)[-1] == ("⬅️ Мои боты", "my_bots")
    assert pairs(failure_keyboard) == [("🤖 Мои боты", "my_bots")]
    assert pairs(activated_keyboard) == [("🤖 Мои боты", "my_bots")]
    management_callbacks = [button.get("callback_data") for row in management_keyboard for button in row if button.get("callback_data")]
    assert management_callbacks[-1] == "my_bots"
    assert "custom_bot_delete:owned-bot" in management_callbacks
    assert pairs(refresh_keyboard) == [("❌ Отмена", "custom_bot_refresh_cancel")]


@pytest.mark.asyncio
async def test_bot_management_render_is_owner_scoped(db):
    owner, other = Admin(external_id="owner"), Admin(external_id="other")
    db.add_all([owner, other])
    await db.flush()
    bot = await add_bot(db, owner, bot_id="private-bot", name="Private bot", username="@private")

    text, keyboard = await menu.render_screen(db, other, f"custom_bot:{bot.id}")

    assert text == "Бот не найден."
    assert pairs(keyboard) == [("⬅️ Назад", "my_bots")]


@pytest.mark.asyncio
async def test_my_bots_keeps_promotion_and_management_paths_for_owned_bot(db):
    owner = Admin(external_id="owner")
    db.add(owner)
    await db.flush()
    bot = await add_bot(db, owner, bot_id="agata", name="Agata", username="@agata")

    _, list_keyboard = await menu.render_screen(db, owner, "my_bots")
    promotion_text, promotion_keyboard = await menu.render_screen(db, owner, f"promotion_bot:{bot.id}")
    management_text, management_keyboard = await menu.render_screen(db, owner, f"custom_bot:{bot.id}")

    assert pairs(list_keyboard)[:2] == [
        ("🟡 Agata · @agata", f"promotion_bot:{bot.id}"),
        ("⚙️ Управление", f"custom_bot:{bot.id}"),
    ]
    assert promotion_text.startswith("📣 Продвижение")
    assert [callback for _, callback in pairs(promotion_keyboard)] == [
        f"promotion_bot_yandex:{bot.id}", f"ads_bot:{bot.id}",
        f"promotion_bot_analytics:{bot.id}", "my_bots",
    ]
    assert management_text.startswith("🤖 Управление ботом")
    assert f"custom_bot_delete:{bot.id}" in callback_values(management_keyboard)
    assert "my_bots" in callback_values(management_keyboard)


@pytest.mark.asyncio
async def test_promotion_bot_callback_from_old_message_still_opens_promotion():
    class Repo:
        def __init__(self):
            self.rendered = []

        async def bot_owned(self, user_id, bot_id):
            return user_id == "owner" and bot_id == "agata"

        async def render(self, user_id, screen, channel_id=None):
            self.rendered.append((user_id, screen, channel_id))
            return "promotion screen", [], None, []

        async def service_message_id(self, user_id):
            return None

    class Client:
        async def answer_callback(self, callback_id, message=None, **kwargs):
            pass

    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime.repo = Repo()
    runtime.client = Client()
    runtime._recent_callback_ids = {}
    rendered = []

    async def render(user_id, screen, *args, **kwargs):
        rendered.append((user_id, screen))

    runtime._render = render
    await runtime._callback(MaxUpdate(
        "message_callback", user_id="owner", callback_id="cb-promo",
        button_payload="promotion_bot:agata",
    ))

    assert rendered == [("owner", "promotion_bot:agata")]


@pytest.mark.asyncio
async def test_non_owner_cannot_render_system_bot_or_system_promotion(db, monkeypatch):
    monkeypatch.setattr(menu.settings, "maxibot_owner_user_id", "owner")
    user = Admin(external_id="ordinary")
    db.add(user)
    await db.flush()

    _, keyboard = await menu.render_screen(db, user, "my_bots")
    text, _ = await menu.render_screen(db, user, "promotion_maxibot")

    assert all(callback != "promotion_maxibot" for _, callback in pairs(keyboard))
    assert "Системный счётчик" not in text
    assert "📣 Продвижение" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["my_bots", "bots"])
async def test_bot_list_callbacks_open_my_bots_and_clear_pending_state(action):
    class Repo:
        def __init__(self):
            self.rendered = []
            self.state_updates = []

        async def set_custom_bot_state(self, user_id, state):
            self.state_updates.append((user_id, state))

        async def render(self, user_id, screen, channel_id=None):
            self.rendered.append((user_id, screen, channel_id))
            return "bot list", [[{"text": "add", "callback_data": "connect_bot"}]], None, []

        async def service_message_id(self, user_id):
            return None

    class Client:
        def __init__(self):
            self.answered = []

        async def answer_callback(self, callback_id, message=None):
            self.answered.append((callback_id, message))

    repo, client = Repo(), Client()
    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime.repo = repo
    runtime.client = client
    runtime._recent_callback_ids = {}

    await runtime._callback(MaxUpdate(
        "message_callback", user_id="owner", callback_id="cb-1", button_payload=action,
    ))

    assert repo.rendered == [("owner", "my_bots", None)]
    assert repo.state_updates == [("owner", None)]
    assert client.answered[0][0] == "cb-1"


@pytest.mark.asyncio
async def test_add_bot_callback_opens_the_existing_connection_intro():
    class Repo:
        async def service_message_id(self, user_id):
            return None

    class Client:
        def __init__(self):
            self.answered = []

        async def answer_callback(self, callback_id, message=None, **kwargs):
            self.answered.append(callback_id)

    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime.repo = Repo()
    runtime.client = Client()
    runtime._recent_callback_ids = {}
    rendered = []

    async def render(user_id, screen, *args, **kwargs):
        rendered.append((user_id, screen))

    runtime._render = render
    await runtime._callback(MaxUpdate(
        "message_callback", user_id="owner", callback_id="cb-add", button_payload="connect_bot",
    ))

    assert runtime.client.answered == ["cb-add"]
    assert rendered == [("owner", "connect_bot_intro")]


def test_legacy_bot_promotion_callback_remains_supported():
    source = open("app/bot/menu.py", encoding="utf-8").read()
    runtime_source = open("app/bot/runtime.py", encoding="utf-8").read()
    assert 'if screen.startswith("promotion_bot:"):' in source
    assert 'elif action.startswith("promotion_bot"):' in runtime_source
