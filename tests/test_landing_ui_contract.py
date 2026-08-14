from pathlib import Path


MAIN_SOURCE = (Path(__file__).parents[1] / "app" / "main.py").read_text()


def test_landing_renders_branded_ui_and_auto_redirects_without_required_click():
    assert "Переход в MAX" in MAIN_SOURCE
    assert "MAXIBOT" in MAIN_SOURCE
    assert "location.replace(target)" in MAIN_SOURCE
    assert 'id="continue"' not in MAIN_SOURCE
    assert "Перейти ↗" not in MAIN_SOURCE
    assert "setTimeout" in MAIN_SOURCE
    assert "clickUrl" not in MAIN_SOURCE


def test_landing_records_visit_and_redirect_before_html_response():
    landing_start = MAIN_SOURCE.index("async def _landing_page")
    landing_source = MAIN_SOURCE[landing_start : MAIN_SOURCE.index("@app.post(\"/traffic/events/browser\")")]
    assert "utm_source=params.get(\"utm_source\")" in landing_source
    assert "yclid=params.get(\"yclid\")" in landing_source
    assert "visit.redirect_at = visit.redirect_at or redirect_at" in landing_source
    assert "send('REDIRECT_STARTED')" in landing_source
    assert "await db.commit()" in landing_source


def test_landing_target_is_scoped_to_its_channel_and_has_no_fallback():
    landing_start = MAIN_SOURCE.index("async def _landing_page")
    landing_source = MAIN_SOURCE[landing_start : MAIN_SOURCE.index("@app.post(\"/traffic/events/browser\")")]
    assert "TrafficProfile.channel_id == landing.channel_id" in landing_source
    assert "f\"https://max.ru/{channel.external_id" not in landing_source
    assert "if not target_is_valid" in landing_source


def test_legacy_redirect_also_rejects_missing_profile_target():
    redirect_start = MAIN_SOURCE.index("async def _traffic_redirect")
    redirect_source = MAIN_SOURCE[redirect_start : MAIN_SOURCE.index("@app.get(\"/go/{public_id}\"")]
    assert "target = profile.target_url if profile else None" in redirect_source
    assert "closed MAX channel target is not configured" in redirect_source
