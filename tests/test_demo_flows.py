import pytest
from app.integrations.max_client import StubMaxClient
from app.bot.router import MenuRouter

class Repo:
    def __init__(self): self.saved=None
    async def screen(self, admin_id, screen, **kwargs): return screen, []
    async def save_service_message(self, admin_id, message_id): self.saved=message_id

@pytest.mark.asyncio
async def test_first_menu_creates_service_message():
    client, repo = StubMaxClient(), Repo()
    message_id = await MenuRouter(client, repo).render("admin", "home")
    assert message_id == "1"
    assert repo.saved == "1"

@pytest.mark.asyncio
async def test_existing_menu_is_edited_without_new_message():
    client, repo = StubMaxClient(), Repo()
    router = MenuRouter(client, repo)
    await router.render("admin", "home")
    message_id = await router.render("admin", "channels", "1")
    assert message_id == "1"
    assert client.seq == 1
    assert client.messages[("admin", "1")][0] == "channels"

@pytest.mark.asyncio
async def test_menu_edit_falls_back_to_new_message():
    client, repo = StubMaxClient(fail_edits=True), Repo()
    message_id = await MenuRouter(client, repo).render("admin", "channels", "old")
    assert message_id == "1"
    assert repo.saved == "1"

@pytest.mark.asyncio
async def test_stub_delivery_is_recorded():
    client = StubMaxClient()
    await client.send_to_channel_user("user-1", "Ответ")
    assert client.deliveries == [("user-1", "Ответ")]

@pytest.mark.asyncio
async def test_stub_forwarded_post_returns_channel_data():
    client = StubMaxClient()
    result = await client.validate_forwarded_post(type("M", (), {"channel_id": "c1"})())
    assert result == {"external_id": "c1", "title": "Новый канал"}
