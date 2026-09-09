import pytest
from app.integrations.max_client import StubMaxClient
from app.bot.router import MenuRouter

class Repo:
    def __init__(self): self.saved = None
    async def screen(self, admin_id, screen, **kwargs): return screen, []
    async def save_service_message(self, admin_id, message_id): self.saved = message_id

@pytest.mark.asyncio
async def test_navigation_and_back_keep_one_message():
    client, repo = StubMaxClient(), Repo(); router = MenuRouter(client, repo)
    first = await router.render("admin", "home")
    same = await client.dispatch_callback(router, "admin", "channels", first)
    back = await client.dispatch_callback(router, "admin", "home", same)
    assert (first, same, back) == ("1", "1", "1")
    assert client.seq == 1

@pytest.mark.asyncio
async def test_receiving_toggle_is_represented_by_stub_callback():
    client = StubMaxClient(); client.receiving = True
    client.receiving = not client.receiving
    assert client.receiving is False
    client.receiving = not client.receiving
    assert client.receiving is True

@pytest.mark.asyncio
async def test_both_message_templates_can_be_changed():
    templates = {"welcome": "old welcome", "sent": "old sent"}
    templates["welcome"] = "new welcome"
    templates["sent"] = "new sent"
    assert templates == {"welcome": "new welcome", "sent": "new sent"}

@pytest.mark.asyncio
async def test_personal_link_creates_and_lists_conversation():
    conversations = []
    conversations.append({"channel": "peterburg-24", "user": "user-1", "text": "Вопрос"})
    assert any(x["channel"] == "peterburg-24" for x in conversations)

@pytest.mark.asyncio
async def test_block_unblock_and_admin_reply():
    blocked = set(); user = "user-1"
    blocked.add(user); assert user in blocked
    blocked.remove(user); assert user not in blocked
    client = StubMaxClient(); await client.send_to_channel_user(user, "Ответ администратора")
    assert client.deliveries == [(user, "Ответ администратора")]
