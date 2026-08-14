from app.bot.menu import public_landing_url
from app.core.config import settings


def test_public_landing_url_uses_configured_domain_and_one_slash(monkeypatch):
    monkeypatch.setattr(settings, "traffic_public_base_url", "https://go.maxibot.jobjin.ru/")
    assert public_landing_url("77459027312767") == "https://go.maxibot.jobjin.ru/77459027312767"


def test_public_landing_url_rejects_nested_path_segments():
    import pytest

    with pytest.raises(ValueError):
        public_landing_url("abc/def")
