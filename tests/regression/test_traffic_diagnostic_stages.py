from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_SOURCE = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
MINI_SOURCE = (ROOT / "miniapp" / "traffic.html").read_text(encoding="utf-8")


def test_diagnostic_funnel_stages_are_persisted_idempotently_without_identity():
    assert "async def _traffic_diagnostic_event" in MAIN_SOURCE
    assert 'dedupe = f"diagnostic:{event_type}:{visit.id}"' in MAIN_SOURCE
    assert "max_user_id=None" in MAIN_SOURCE
    for stage in (
        "LANDING_RENDERED", "STARTAPP_READY", "REDIRECT_STARTED", "MINI_APP_LOADED", "RESOLVE_OK",
        "JS_LANDING_STARTED", "MAX_LINK_ASSIGNED", "PAGE_HIDDEN", "PAGE_RESTORED",
        "MINIAPP_CHANNEL_HANDOFF_STARTED", "MINIAPP_PAGE_HIDDEN", "MINIAPP_PAGE_RESTORED",
        "channel_open_sent",
        "MINIAPP_FALLBACK_SHOWN", "MINIAPP_FALLBACK_CLICKED",
    ):
        assert f'"{stage}"' in MAIN_SOURCE


def test_diagnostics_use_low_cardinality_safe_context_only():
    assert "_diagnostic_device_family" in MAIN_SOURCE
    assert "_diagnostic_referrer_host" in MAIN_SOURCE
    assert "_safe_browser_signals" in MAIN_SOURCE
    assert '"signals": _safe_browser_signals(payload.raw_signals)' in MAIN_SOURCE
    assert '"raw_signals": payload.raw_signals' not in MAIN_SOURCE


def test_browser_handoff_stages_are_idempotent_per_visit():
    browser_endpoint = MAIN_SOURCE[MAIN_SOURCE.index('@app.post("/traffic/events/browser")'):MAIN_SOURCE.index('@app.get("/traffic-test")')]
    assert '"MAX_REDIRECT_ATTEMPTED"' in browser_endpoint
    assert '"PAGEHIDE"' in browser_endpoint
    assert '"BEFOREUNLOAD"' in browser_endpoint
    assert 'f"browser-stage:{visit.id}:{payload.event_type}"' in browser_endpoint


def test_miniapp_stages_require_verified_max_identity_and_cannot_convert():
    endpoint = MAIN_SOURCE[MAIN_SOURCE.index('@app.post("/api/traffic/miniapp-event")'):MAIN_SOURCE.index('@app.post("/api/traffic/channel-open")')]
    assert "_traffic_identity(x_max_init_data, db)" in endpoint
    assert "MINIAPP_CHANNEL_HANDOFF_STARTED" in endpoint
    assert "MINIAPP_PAGE_HIDDEN" in endpoint
    assert "MINIAPP_PAGE_RESTORED" in endpoint
    assert "OfflineConversion" not in endpoint
    assert "TrafficMembership" not in endpoint


def test_telemetry_contract_has_safe_untrusted_pre_init_path_and_bounded_dedupe():
    assert "async def traffic_miniapp_telemetry" in MAIN_SOURCE
    assert "trusted=trusted" in MAIN_SOURCE
    assert "start_param" in MAIN_SOURCE
    assert "telemetry_count" in MAIN_SOURCE
    assert "if int(telemetry_count) >= 64" in MAIN_SOURCE
    assert "max_user_id=None" in MAIN_SOURCE
    assert "raw yclid/startapp/initData/MAX user_id/cookies/UA/referrer are not returned" in MAIN_SOURCE


def test_telemetry_error_and_timing_contract_is_aggregate_only():
    report = MAIN_SOURCE[MAIN_SOURCE.index('@app.get("/admin/traffic/telemetry-report")'):MAIN_SOURCE.index('@app.get("/api/channels")')]
    for name in ("final_status_counts", "device_family", "browser_family", "trusted_events", "errors", "timings"):
        assert name in report
    assert "device_stage_counts" in report
    assert "device_final_status_counts" in report
    assert '"join_request", "join_request_created", "pending_first_seen", "user_added"' in report
    for name in ("landing_to_redirect_ms", "redirect_to_miniapp_ms", "identity_to_bind_ms"):
        assert name in report


def test_telemetry_event_allowlist_covers_landing_miniapp_and_errors():
    for event in (
        "MAX_REDIRECT_ATTEMPTED", "PAGE_HIDDEN", "FOCUS_LOST", "WEB_FALLBACK_CLICKED",
        "MINIAPP_JS_STARTED", "INIT_DATA_VALID", "BIND_SUCCESS", "CHANNEL_OPEN_FAILED", "CLIENT_ERROR",
    ):
        assert f'"{event}"' in MAIN_SOURCE


def test_miniapp_telemetry_is_nonblocking_and_does_not_change_handoff_contract():
    assert "/api/traffic/miniapp-telemetry" in MINI_SOURCE
    assert "MINIAPP_JS_STARTED" in MINI_SOURCE
    assert "MAX_WEBAPP_OBJECT_AVAILABLE" in MINI_SOURCE
    assert "INIT_DATA_PRESENT" in MINI_SOURCE
    assert "START_PARAM_PRESENT" in MINI_SOURCE
    assert "CHANNEL_OPEN_REQUESTED" in MINI_SOURCE
    assert "MINIAPP_FALLBACK_SHOWN" in MINI_SOURCE
    assert "MINIAPP_FALLBACK_CLICKED" in MINI_SOURCE
    assert "CLIENT_ERROR" in MINI_SOURCE
    assert "keepalive: true" in MINI_SOURCE
    assert "setTimeout(() => controller.abort(), 1000)" in MINI_SOURCE
    assert "navigator.platform" not in MINI_SOURCE
    # Telemetry must not introduce a second navigation mechanism.
    telemetry_start = MINI_SOURCE.index("function miniTelemetry")
    telemetry_end = MINI_SOURCE.index("function miniappSignal")
    telemetry_block = MINI_SOURCE[telemetry_start:telemetry_end]
    assert "window.location" not in telemetry_block
    assert "setTimeout(() => controller.abort(), 1000)" in telemetry_block
    assert "<iframe" not in telemetry_block


def test_mini_route_records_load_before_verified_resolve():
    mini = MAIN_SOURCE[MAIN_SOURCE.index('@app.get("/mini"'):MAIN_SOURCE.index('@app.get("/traffic-test/app"')]
    assert "WebAppStartParam" in mini
    assert '"MINI_APP_LOADED"' in mini
    assert "_traffic_diagnostic_event" in mini
    assert "await db.commit()" in mini


def test_diagnostics_do_not_create_attribution_or_conversion():
    helper = MAIN_SOURCE[MAIN_SOURCE.index("async def _traffic_diagnostic_event"):MAIN_SOURCE.index('@app.get("/health")')]
    assert "MaxIdentityLink" not in helper
    assert "OfflineConversion" not in helper
    assert "max_user_id=None" in helper


def test_admin_diagnostics_expose_presence_and_stages_not_raw_identifiers():
    debug = MAIN_SOURCE[MAIN_SOURCE.index('@app.get("/admin/traffic-debug")'):MAIN_SOURCE.index('@app.get("/admin/traffic/attributions")')]
    for field in ("yclid_present", "startapp_present", "max_identity_linked", "diagnostic_stages"):
        assert f'"{field}"' in debug
    assert '"yclid": visit.yclid' not in debug
    assert '"startapp_token": visit.startapp_token' not in debug
    assert '"max_user_id": visit.max_user_id' not in debug
