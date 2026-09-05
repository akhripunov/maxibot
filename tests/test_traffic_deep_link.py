from app.traffic.deep_link import build_bot_deep_link, build_miniapp_deep_link, build_native_miniapp_deep_link, build_web_bot_deep_link, build_web_miniapp_deep_link, new_attribution_token, valid_max_target


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


def test_web_bot_deep_link_is_browser_compatible_and_keeps_opaque_token():
    link = build_web_bot_deep_link("@maxibot", "opaque-token-A")
    assert link == "https://web.max.ru/maxibot?start=opaque-token-A"
    assert "yclid" not in link
    assert "channel_id" not in link


def test_miniapp_deep_link_uses_startapp_and_opaque_token():
    link = build_miniapp_deep_link("@maxibot", "opaque-token-A")
    assert link == "https://max.ru/maxibot?startapp=opaque-token-A"
    assert "yclid" not in link
    assert "channel_id" not in link


def test_native_miniapp_deep_link_uses_max_scheme_and_same_opaque_token():
    link = build_native_miniapp_deep_link("@maxibot", "opaque-token-A")
    assert link == "max://max.ru/maxibot?startapp=opaque-token-A"
    assert "yclid" not in link


def test_web_miniapp_deep_link_uses_startapp_and_same_opaque_token():
    link = build_web_miniapp_deep_link("@maxibot", "opaque-token-A")
    assert link == "https://web.max.ru/maxibot?startapp=opaque-token-A"
    assert "yclid" not in link


def test_target_validation_is_strict():
    assert valid_max_target("https://max.ru/join/example")
    assert not valid_max_target("https://max.ru/")
    assert not valid_max_target("https://evil.example/join/example")
    assert not valid_max_target(None)


def test_landing_routes_to_channel_preview_and_keeps_opaque_token_server_side():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    assert "settings.traffic_bot_attribution_enabled" in source
    assert "startapp_token=new_attribution_token()" in source
    assert 'return await _traffic_redirect(landing.public_id, token, db, via_bot=True, request=request)' in source
    assert "async def _traffic_redirect" in source
    assert "build_web_miniapp_deep_link" in source
    assert "build_native_miniapp_deep_link" in source
    redirect = source[source.index("async def _traffic_redirect"):source.index('@app.get("/go/{public_id}"')]
    assert "build_native_miniapp_deep_link if _is_mobile_request(request) else build_web_miniapp_deep_link" in redirect
    assert "window.location.replace" in source


def test_channel_preview_cta_does_not_force_maxibot_intermediate():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    click = source[source.index("async def channel_tracking_preview_click"):source.index("@app.get(\"/go/{public_id}/click\"")]
    assert "via_bot=settings.traffic_bot_attribution_enabled" in click
    assert "build_web_miniapp_deep_link" in source
    assert "build_native_miniapp_deep_link" in source
    assert "settings.max_bot_username" in source
    assert "settings.max_miniapp_url" in source


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


def test_atomic_traffic_resolve_binds_identity_and_returns_strict_target():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    section = source[source.index("async def traffic_resolve"):source.index("async def traffic_channel_open")]
    assert 'await _traffic_event(db, visit, "max_identity_linked"' in section
    assert 'await _traffic_event(db, visit, "channel_open_clicked"' in section
    assert "valid_max_target(profile.target_url)" in section
    assert "await db.commit()" in section
    assert '"target_url": target' in section
    assert "f\"https://max.ru/{channel.external_id" not in section


def test_verified_resolve_creates_only_a_server_side_join_intent_after_target_validation():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    section = source[source.index("async def traffic_resolve"):source.index('@app.post("/api/traffic/miniapp-event")')]
    assert "await activate_join_intent(" in section
    assert section.index("if not target:") < section.index("await activate_join_intent(")
    assert "startapp_token" not in section[section.index("await activate_join_intent("):]


def test_traffic_miniapp_is_silent_and_opens_channel_automatically():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "miniapp" / "traffic.html").read_text()
    assert "openChannel(data.target_url)" in source
    assert "api('/api/traffic/resolve'" in source
    assert "api('/api/traffic/identify'" not in source
    assert "api('/api/traffic/channel-open'" not in source
    assert "platform === 'android'" in source
    assert "webApp.openMaxLink(targetUrl)" in source
    assert "typeof webApp?.openMaxLink === 'function'" in source
    assert "const isAppleMobile = /iPhone|iPad|iPod/i.test(navigator.userAgent)" in source
    assert "!isAppleMobile && typeof webApp?.openMaxLink === 'function'" in source
    assert "channelFallbackTimer" not in source
    assert "openViaAnchor(targetUrl)" in source
    assert "link.target = '_blank'" in source
    assert "link.click()" in source
    channel_handoff = source[source.index("function openChannel"):source.index("async function resolveChannel")]
    assert "webApp.close()" not in channel_handoff
    assert "Открыть канал" not in source
    assert "Проверить подписку" not in source
    assert "slowTimer" in source
    assert "fallbackTimer" in source
    assert "visibility:hidden" in source
    assert "main.visible{visibility:visible}" in source
    assert "setTimeout(() => mainEl.classList.add('visible'), 1600)" in source


def test_production_miniapp_alias_is_present_for_registered_startapp_url():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    assert '@app.get("/mini", response_class=FileResponse)' in source
    mini = source[source.index('@app.get("/mini", response_class=FileResponse)'):source.index('@app.get("/traffic-test/app"')]
    assert 'FileResponse(mini_dir / "traffic.html"' in mini
    assert 'FileResponse(mini_dir / "index.html"' in mini
    assert 'if not startapp:' in mini
