from app.core.config import settings
from app.traffic.system_conversion import system_conversion_goal


def test_system_conversion_goal_mapping_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "system_conversion_goal_map", "bot_started=registered_bot,maxibot_channel_connected=connected")
    assert system_conversion_goal("bot_started") == "registered_bot"
    assert system_conversion_goal("maxibot_channel_connected") == "connected"


def test_unknown_internal_event_falls_back_without_changing_attribution_core(monkeypatch):
    monkeypatch.setattr(settings, "system_conversion_goal_map", "bot_started=registered_bot")
    assert system_conversion_goal("unmapped_event") == "unmapped_event"
