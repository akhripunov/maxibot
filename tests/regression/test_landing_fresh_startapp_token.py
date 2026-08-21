from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[2] / "app/main.py").read_text()
MENU_SOURCE = (Path(__file__).resolve().parents[2] / "app/bot/menu.py").read_text()
RUNTIME_SOURCE = (Path(__file__).resolve().parents[2] / "app/bot/runtime.py").read_text()


def test_expired_unbound_startapp_visit_is_not_reused_by_landing():
    landing = SOURCE[SOURCE.index("async def _landing_page"):SOURCE.index("async def _traffic_redirect")]
    assert "visit.status = \"EXPIRED\"" in landing
    assert "visit = None" in landing
    assert "settings.traffic_startapp_ttl_seconds" in landing
    assert "startapp_token=new_attribution_token()" in landing


def test_landing_keeps_target_scoped_to_its_channel():
    landing = SOURCE[SOURCE.index("async def _landing_page"):SOURCE.index("async def _traffic_redirect")]
    assert "TrafficProfile.channel_id == landing.channel_id" in landing


def test_ios_duplicate_stale_payload_reuses_recent_bound_visit_for_same_user():
    binding = MENU_SOURCE[
        MENU_SOURCE.index("async def bind_traffic_startapp"):
        MENU_SOURCE.index("async def mark_traffic_to_channel")
    ]
    assert "TrafficVisit.max_user_id == str(max_user_id)" in binding
    assert "TrafficVisit.channel_id == visit.channel_id" in binding
    assert "TRAFFIC_STARTAPP_STALE_HANDOFF recent_bound_visit" in binding


def test_unknown_ios_handoff_recovers_only_one_recent_bound_channel():
    recovery = MENU_SOURCE[
        MENU_SOURCE.index("async def recover_recent_traffic_start"):
        MENU_SOURCE.index("async def mark_traffic_to_channel")
    ]
    assert "TrafficVisit.max_user_id == str(max_user_id)" in recovery
    assert "timedelta(minutes=5)" in recovery
    assert "len(channel_ids) != 1" in recovery
    assert "recover_recent_traffic_start(str(user_id))" in RUNTIME_SOURCE
    assert "TRAFFIC_STARTAPP_IOS_RECOVERED" in RUNTIME_SOURCE


def test_duplicate_unknown_payload_does_not_emit_a_channel_cta():
    start = RUNTIME_SOURCE[
        RUNTIME_SOURCE.index("async def _start"):
        RUNTIME_SOURCE.index("def _callback_replacement_body")
    ]
    assert "if now - item[0] < 300" in start
    assert "TRAFFIC_STARTAPP_BOUND_SILENT" in start
    assert '"Открыть канал"' not in start
    assert "TRAFFIC_STARTAPP_DUPLICATE_STALE_IGNORED" in start


def test_old_payload_routes_only_when_already_bound_to_same_user():
    resolver = MENU_SOURCE[
        MENU_SOURCE.index("async def resolve_bound_traffic_token"):
        MENU_SOURCE.index("async def mark_traffic_to_channel")
    ]
    assert "MaxIdentityLink.max_user_id == str(max_user_id)" in resolver
    assert "visit.max_user_id != str(max_user_id)" in resolver
    assert '"status": "ROUTING_ONLY"' in resolver
    assert "resolve_bound_traffic_token(traffic_token, str(user_id))" in RUNTIME_SOURCE
    assert "TRAFFIC_STARTAPP_BOUND_TOKEN_ROUTE" in RUNTIME_SOURCE
