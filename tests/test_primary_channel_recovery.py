from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import menu
from app.bot.menu import MenuRepository, official_bot_added_channel, render_screen
from app.bot.runtime import MaxBotRuntime
from app.db.models import Admin, Base, Channel, LandingPage, TrafficProfile
from app.integrations.max_client import MaxApiError, MaxUpdate, RealMaxClient


def admins(*, owner_id=101, bot_admin=True):
    members = [{"user_id": owner_id, "is_owner": True}]
    if bot_admin:
        members.append({"user_id": 202, "is_admin": True})
    return {"members": members}


@pytest.mark.parametrize(
    ("owner_id", "link", "actor"),
    [(101, None, "101"), (101, "https://max.ru/channel", None), (None, None, None)],
)
def test_official_channel_accepts_nullable_actor_and_link(owner_id, link, actor):
    result = official_bot_added_channel(
        {"type": "channel", "owner_id": owner_id, "link": link},
        actor, admins=admins(), bot_user_id=202,
    )
    assert result["owner_user_id"] == "101"
    assert result["target_url"] == link


@pytest.mark.parametrize(
    ("chat", "actor", "members"),
    [
        ({"owner_id": 303}, None, admins()),
        ({"owner_id": 101}, "303", admins()),
        ({"owner_id": 101}, None, admins(bot_admin=False)),
        ({"owner_id": 101, "link": "https://example.com"}, None, admins()),
        ({"owner_id": 101}, None, {"members": admins()["members"] + [{"user_id": 303, "is_owner": True}]}),
    ],
)
def test_official_channel_fails_closed(chat, actor, members):
    assert official_bot_added_channel(
        {"type": "channel", **chat}, actor, admins=members, bot_user_id=202,
    ) is None


async def test_event_and_owner_recheck_share_registration_and_preserve_existing_invite(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(menu, "connection_status", AsyncMock(return_value=None))
        monkeypatch.setattr(menu, "channel_setup_status", AsyncMock(return_value={}))
        client = SimpleNamespace(
            get_chat=AsyncMock(return_value={"type": "channel", "title": "Тестовый канал"}),
            get_admins=AsyncMock(return_value=admins()),
            get_me=AsyncMock(return_value={"user_id": 202}),
            send_message=AsyncMock(),
        )
        runtime = MaxBotRuntime(client, sessions)
        runtime._render = AsyncMock()
        runtime.repo.queue_system_channel_connected = AsyncMock(return_value="queued")
        parser = RealMaxClient(token="fixture")
        event = parser._parse_update({"update_type": "bot_added", "chat_id": -404, "is_channel": True})
        assert event.user_id is None
        await runtime.handle(event)
        async with sessions() as db:
            owner = await db.scalar(select(Admin).where(Admin.external_id == "101"))
            assert owner
            _, keyboard = await render_screen(db, owner, "promotion")
            assert any(button["text"] == "Тестовый канал" for row in keyboard for button in row)
            channel = await db.scalar(select(Channel))
            landing = await db.scalar(select(LandingPage))
            landing_url = menu.public_landing_url(landing.public_id)
            for screen in ("promotion_channel", "promotion_landing"):
                message, controls = await render_screen(db, owner, f"{screen}:{channel.id}")
                assert landing_url not in message
                assert all(button.get("url") != landing_url for row in controls for button in row)
            readiness, _ = await render_screen(db, owner, f"promotion_settings:{channel.id}")
            assert "Readiness заблокирован" in readiness
            assert (await db.scalar(select(TrafficProfile))).target_url is None

        async def recheck(message_id):
            retry = parser._parse_update({
                "update_type": "message_created",
                "message": {
                    "sender": {"user_id": 101, "name": "Владелец"},
                    "recipient": {"user_id": 202, "chat_type": "dialog"},
                    "body": {"mid": message_id, "text": "/connect_channel -404"},
                },
            })
            await runtime.handle(retry)

        await recheck("first")
        client.get_chat.return_value["link"] = "https://max.ru/channel"
        await recheck("second")
        client.get_chat.return_value.pop("link")
        await runtime.handle(event)
        await recheck("third")
        async with sessions() as db:
            for model in (Channel, LandingPage, TrafficProfile):
                assert await db.scalar(select(func.count()).select_from(model)) == 1
            assert (await db.scalar(select(TrafficProfile))).target_url == "https://max.ru/channel"
            owner = await db.get(Admin, owner.id)
            message, _ = await render_screen(db, owner, f"promotion_landing:{channel.id}")
            assert landing_url in message
        runtime.repo.queue_system_channel_connected.assert_awaited_with("101")
    finally:
        await engine.dispose()


async def test_manual_check_does_not_allow_a_different_user_to_claim_the_channel():
    client = SimpleNamespace(
        get_chat=AsyncMock(return_value={"type": "channel", "title": "Тестовый канал"}),
        get_admins=AsyncMock(return_value=admins()),
        get_me=AsyncMock(return_value={"user_id": 202}),
        send_message=AsyncMock(),
    )
    runtime = MaxBotRuntime(client, None)
    runtime.repo = AsyncMock(spec=MenuRepository)
    await runtime._channel_admin_done("303", "-404")
    runtime.repo.connect_channel_from_bot_added.assert_not_awaited()



@pytest.mark.parametrize("admin_response", [MaxApiError(403, "no access"), admins(bot_admin=False)])
async def test_initial_event_can_offer_owner_recheck_before_admin_grant(admin_response):
    client = SimpleNamespace(
        get_chat=AsyncMock(return_value={"type": "channel", "owner_id": 101}),
        get_admins=AsyncMock(),
        get_me=AsyncMock(return_value={"user_id": 202}),
        send_message=AsyncMock(),
        answer_callback=AsyncMock(),
    )
    if isinstance(admin_response, MaxApiError):
        client.get_admins.side_effect = admin_response
    else:
        client.get_admins.return_value = admin_response
    runtime = MaxBotRuntime(client, None)
    runtime.repo = AsyncMock(spec=MenuRepository)
    await runtime.handle(MaxUpdate(update_type="bot_added", chat_id="-404", raw={"is_channel": True}))
    runtime.repo.connect_channel_from_bot_added.assert_not_awaited()
    recipient, _, buttons = client.send_message.await_args.args
    assert recipient == "101"
    assert buttons[0][0]["callback_data"] == "channel_admin_done:-404"

    runtime._channel_admin_done = AsyncMock()
    parser = RealMaxClient(token="fixture")
    await runtime.handle(parser._parse_update({
        "update_type": "message_callback",
        "callback": {"callback_id": "grant-1", "user": {"user_id": 101}, "payload": buttons[0][0]["callback_data"]},
    }))
    runtime._channel_admin_done.assert_awaited_once_with("101", "-404")
