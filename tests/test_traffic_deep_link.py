from app.traffic.deep_link import build_bot_deep_link, new_attribution_token, valid_max_target


def test_attribution_token_is_opaque_and_short_enough_for_max_payload():
    token = new_attribution_token()
    assert 20 <= len(token) <= 96
    assert "yclid" not in token.lower()
    assert "user" not in token.lower()
    assert token != new_attribution_token()


def test_bot_deep_link_contains_only_encoded_opaque_token():
    link = build_bot_deep_link("@maxibot", "opaque-token-A")
    assert link == "https://max.ru/maxibot?start=opaque-token-A"
    assert "yclid" not in link
    assert "channel_id" not in link


def test_target_validation_is_strict():
    assert valid_max_target("https://max.ru/join/example")
    assert not valid_max_target("https://max.ru/")
    assert not valid_max_target("https://evil.example/join/example")
    assert not valid_max_target(None)


def test_landing_has_opt_in_bot_route_and_server_side_binding_contract():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    assert "settings.traffic_bot_attribution_enabled" in source
    assert "startapp_token=new_attribution_token()" in source
    assert "build_bot_deep_link(settings.max_bot_username" in source


def test_runtime_rejects_unconfigured_channel_target_without_fallback():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    assert "valid_max_target(profile.target_url)" in source
    assert "f\"https://max.ru/{channel.external_id" not in source


def test_channel_open_endpoint_has_no_public_profile_fallback():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    section = source[source.index("async def traffic_channel_open"):source.index("async def traffic_check_subscription")]
    assert "valid_max_target(profile.target_url)" in section
    assert "f\"https://max.ru/{channel.external_id" not in section
