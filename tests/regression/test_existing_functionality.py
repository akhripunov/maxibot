"""Small, stable baseline for functionality that must survive every release."""

from app.domain.services import channel_card_text, menu_text
from app.traffic.deep_link import build_bot_deep_link, valid_max_target


def test_tc_traffic_008_public_channel_target_is_strictly_validated():
    assert valid_max_target("https://max.ru/join/regression-channel")
    assert not valid_max_target("https://example.com/join/regression-channel")
    assert not valid_max_target("https://max.ru/")


def test_tc_traffic_004_bot_deep_link_keeps_opaque_payload_contract():
    assert build_bot_deep_link("@maxibot", "regression-token") == "https://max.ru/maxibot?start=regression-token"


def test_tc_bot_001_main_menu_and_channel_card_remain_renderable():
    assert "MAXIBOT" in menu_text(1, 1, 1)
    assert "Regression channel" in channel_card_text("Regression channel", True, 0, 0, "2026-08-13", "https://max.ru/regression")
