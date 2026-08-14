import pytest

from app.bot.runtime import MaxBotRuntime
from app.integrations.max_client import MaxMessage, MaxUpdate, StubMaxClient


class CallbackClient(StubMaxClient):
    def __init__(self):
        super().__init__()
        self.answers = []

    async def answer_callback(self, callback_id, message=None):
        self.answers.append((callback_id, message))
        return True


class CallbackRepo:
    async def render(self, user_id, screen, channel_id=None):
        return "Rendered screen", [[{"text": "Home", "callback_data": "home"}]], [], [{"type": "image", "url": "https://example.test/image"}]

    async def resolve_owned_channel_id(self, user_id, reference):
        return reference

    async def service_message_id(self, user_id):
        return "mid-42"

@pytest.mark.asyncio
async def test_navigation_callback_replaces_only_the_clicked_service_message():
    client = CallbackClient()
    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime.client = client
    runtime.repo = CallbackRepo()
    original_render = runtime.repo.render

    runtime.repo.render = original_render
    update = MaxUpdate(
        "message_callback",
        chat_id="admin-1",
        user_id="admin-1",
        callback_id="callback-1",
        button_payload="channels",
        message=MaxMessage("mid-42", "admin-1", "Admin", "old"),
    )

    await runtime._navigation_callback(update, "admin-1", "channels")

    assert client.answers[0][0] == "callback-1"
    assert client.answers[0][1] is None
    assert client.messages[("admin-1", "mid-42")][0] == "Rendered screen"


def test_duplicate_message_created_is_suppressed_by_external_message_id():
    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime._recent_update_keys = {}
    update = MaxUpdate(
        "message_created",
        user_id="user-1",
        message=MaxMessage("message-42", "user-1", "User", "hello"),
    )

    assert runtime._is_duplicate_message_update(update) is False
    assert runtime._is_duplicate_message_update(update) is True
