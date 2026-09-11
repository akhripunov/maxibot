from app.db.models import BotTrafficEvent
import app.bot.menu as menu
from app.bot.runtime import MaxBotRuntime
from app.integrations.max_client import MaxUpdate


def test_system_startapp_menu_path_has_bot_traffic_event_model_import():
    """The real bot_started path must reach menu rendering without NameError."""
    assert menu.BotTrafficEvent is BotTrafficEvent


def test_real_bot_started_reaches_automatic_home_menu():
    class Repo:
        def __init__(self):
            self.bind_calls = 0
            self.reset_calls = 0

        async def bind_system_startapp(self, token, user_id):
            self.bind_calls += 1
            return True

        async def reset_navigation(self, user_id, preserve_service_message=True):
            self.reset_calls += 1

    runtime = MaxBotRuntime.__new__(MaxBotRuntime)
    runtime.repo = Repo()
    runtime._recent_start_keys = {}
    runtime._recent_start_users = {}
    runtime._user_channels = {}
    rendered = []

    async def render(user_id, screen, **kwargs):
        rendered.append((user_id, screen, kwargs))

    runtime._render = render

    import asyncio
    asyncio.run(runtime._start(MaxUpdate("bot_started", user_id="u1", raw={"payload": "opaque"})))

    assert runtime.repo.bind_calls == 1
    assert runtime.repo.reset_calls == 1
    assert rendered == [("u1", "home", {"force_new": True})]


def test_observability_start_path_keeps_conversion_single_and_start_only():
    from pathlib import Path

    root = Path(__file__).parents[1]
    menu_source = (root / "app/bot/menu.py").read_text(encoding="utf-8")
    runtime_source = (root / "app/bot/runtime.py").read_text(encoding="utf-8")
    bind = menu_source[menu_source.index("async def bind_system_startapp"):menu_source.index("async def queue_system_channel_connected")]
    start = runtime_source[runtime_source.index("    async def _start("):runtime_source.index("    async def _callback(")]

    assert '"paid_journey_key": paid_journey_key' in bind
    assert '"internal_event": "bot_started"' in bind
    assert 'await self._render(user_id, "home", force_new=True)' in start
    assert "bind_system_startapp" in start
    assert "BotConversion" not in start


def test_system_start_fallback_never_intercepts_a_nonempty_channel_payload():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/bot/menu.py").read_text(encoding="utf-8")
    bind = source[source.index("async def bind_system_startapp"):source.index("async def queue_system_channel_connected")]
    assert "if not visit and not token:" in bind
