from pathlib import Path


MAIN_SOURCE = (Path(__file__).parents[1] / "app" / "main.py").read_text()


def test_landing_auto_redirects_to_silent_startapp_bridge_without_showing_maxibot_chat():
    assert "Переход в MAX" in MAIN_SOURCE
    landing_start = MAIN_SOURCE.index("async def _landing_page")
    landing_source = MAIN_SOURCE[landing_start : MAIN_SOURCE.index("@app.post(\"/traffic/events/browser\")")]
    assert "return await _traffic_redirect(landing.public_id, token, db, via_bot=True, request=request)" in landing_source
    assert "MAXIBOT tracking deep link" in landing_source


def test_landing_records_visit_before_automatic_channel_redirect():
    landing_start = MAIN_SOURCE.index("async def _landing_page")
    landing_source = MAIN_SOURCE[landing_start : MAIN_SOURCE.index("@app.post(\"/traffic/events/browser\")")]
    assert "utm_source=params.get(\"utm_source\")" in landing_source
    assert "yclid=params.get(\"yclid\")" in landing_source
    assert "settings.traffic_bot_attribution_enabled" in landing_source
    assert "return await _traffic_redirect(landing.public_id, token, db, request=request)" in landing_source
    assert "await db.commit()" in landing_source


def test_landing_disables_browser_cache_for_fresh_tracking_payload():
    landing_start = MAIN_SOURCE.index("async def _landing_page")
    landing_source = MAIN_SOURCE[landing_start : MAIN_SOURCE.index("@app.post(\"/traffic/events/browser\")")]
    assert 'response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"' in landing_source
    assert 'response.headers["Pragma"] = "no-cache"' in landing_source


def test_traffic_landing_does_not_render_suggestion_copy():
    from app.domain.services import promotion_landing_description

    assert promotion_landing_description("✅ Напишите ваш вопрос\\n\\nБот создан с помощью Предложка") == "Переходим в MAX…"
    assert promotion_landing_description("Кастомное описание канала") == "Кастомное описание канала"


def test_traffic_redirect_automatically_opens_miniapp_for_device():
    redirect = MAIN_SOURCE[MAIN_SOURCE.index("async def _traffic_redirect"):MAIN_SOURCE.index('@app.get("/go/{public_id}"')]
    assert '<title>Переход в MAX</title>' in redirect
    assert '<h1>Переход в MAX</h1>' in redirect
    assert "build_web_miniapp_deep_link" in redirect
    assert "build_native_miniapp_deep_link" in redirect
    assert "settings.max_bot_username" in redirect
    assert "link_builder(settings.max_bot_username, visit.startapp_token)" in redirect
    assert "setTimeout" not in redirect
    assert "window.location.replace" in redirect
    assert 'Перейти в канал' in redirect


def test_traffic_redirect_is_device_aware():
    redirect = MAIN_SOURCE[MAIN_SOURCE.index("async def _traffic_redirect"):MAIN_SOURCE.index('@app.get("/go/{public_id}"')]
    assert "_is_mobile_request(request)" in redirect
    assert "build_native_miniapp_deep_link if _is_mobile_request(request) else build_web_miniapp_deep_link" in redirect
    assert "setTimeout" not in redirect
    assert "window.location.replace" in redirect
    assert 'class=\"primary\"' in redirect


def test_landing_target_is_scoped_to_its_channel_and_has_no_fallback():
    landing_start = MAIN_SOURCE.index("async def _landing_page")
    landing_source = MAIN_SOURCE[landing_start : MAIN_SOURCE.index("@app.post(\"/traffic/events/browser\")")]
    assert "TrafficProfile.channel_id == landing.channel_id" in landing_source
    assert "f\"https://max.ru/{channel.external_id" not in landing_source
    assert "if not target_is_valid" in landing_source


def test_channel_preview_uses_only_its_landing_channel_target():
    preview = MAIN_SOURCE[MAIN_SOURCE.index("async def _channel_preview"):MAIN_SOURCE.index("@app.post(\"/traffic/events/browser\")")]
    assert "TrafficProfile.channel_id == landing.channel_id" in preview
    assert "valid_max_target(target)" in preview
    assert "profile.target_url" in preview
    assert "/track/{landing.public_id}/click" in preview


def test_preview_cta_uses_verified_miniapp_path_and_legacy_redirect_stays_scoped():
    redirect = MAIN_SOURCE[MAIN_SOURCE.index("async def _traffic_redirect"):MAIN_SOURCE.index("@app.get(\"/go/{public_id}\"")]
    assert "via_bot: bool = False" in redirect
    assert "build_web_miniapp_deep_link" in redirect
    assert "build_native_miniapp_deep_link" in redirect
    assert "settings.max_bot_username" in redirect
    assert "link_builder(settings.max_bot_username, visit.startapp_token)" in redirect
    assert "setTimeout" not in redirect
    assert "window.location.replace" in redirect
    assert "settings.max_miniapp_url" in redirect
    assert "destination = target" in redirect
    assert "native_channel" in redirect
    click = MAIN_SOURCE[MAIN_SOURCE.index("async def channel_tracking_preview_click"):MAIN_SOURCE.index("@app.get(\"/go/{public_id}/click\"")]
    assert "via_bot=settings.traffic_bot_attribution_enabled" in click


def test_legacy_redirect_also_rejects_missing_profile_target():
    redirect_start = MAIN_SOURCE.index("async def _traffic_redirect")
    redirect_source = MAIN_SOURCE[redirect_start : MAIN_SOURCE.index("@app.get(\"/go/{public_id}\"")]
    assert "target = profile.target_url if profile else None" in redirect_source
    assert "closed MAX channel target is not configured" in redirect_source
