"""Contract for distinguishing a handoff attempt from a real Mini App launch."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_SOURCE = (ROOT / "app" / "main.py").read_text(encoding="utf-8")


def test_handoff_attempt_is_recorded_without_new_visit_or_token():
    redirect = MAIN_SOURCE[MAIN_SOURCE.index("async def _traffic_redirect") : MAIN_SOURCE.index('@app.get("/go/{public_id}"')]
    assert '"HANDOFF_ATTEMPTED"' in redirect
    assert '"REDIRECT_STARTED"' in redirect
    assert "new_attribution_token()" in redirect
    assert "await db.commit()" in redirect


def test_handoff_confirmation_requires_real_mini_route():
    mini = MAIN_SOURCE[MAIN_SOURCE.index('@app.get("/mini"') : MAIN_SOURCE.index('@app.get("/traffic-test/app"')]
    assert '"HANDOFF_CONFIRMED"' in mini
    assert '"MINI_APP_LOADED"' in mini
    assert "TrafficVisit.startapp_token == startapp" in mini
    assert "HANDOFF_CONFIRMED" not in MAIN_SOURCE[0 : MAIN_SOURCE.index('@app.get("/mini"')]


def test_admin_diagnostics_expose_attempt_and_confirmation_stages():
    debug = MAIN_SOURCE[MAIN_SOURCE.index('@app.get("/admin/traffic-debug"') : MAIN_SOURCE.index('@app.get("/admin/traffic/attributions"')]
    assert '"HANDOFF_ATTEMPTED"' in debug
    assert '"HANDOFF_CONFIRMED"' in debug
    for stage in ("JS_LANDING_STARTED", "MAX_LINK_ASSIGNED", "PAGE_HIDDEN", "PAGE_RESTORED"):
        assert f'"{stage}"' in debug
    assert '"startapp_token":' not in debug
    assert '"max_user_id":' not in debug
