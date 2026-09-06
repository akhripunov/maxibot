from app.db.models import BotTrafficEvent
import app.bot.menu as menu


def test_system_startapp_menu_path_has_bot_traffic_event_model_import():
    """The real bot_started path must reach menu rendering without NameError."""
    assert menu.BotTrafficEvent is BotTrafficEvent
