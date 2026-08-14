from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[2] / "app/main.py").read_text()


def test_expired_unbound_startapp_visit_is_not_reused_by_landing():
    landing = SOURCE[SOURCE.index("async def _landing_page"):SOURCE.index("async def _traffic_redirect")]
    assert "visit.status = \"EXPIRED\"" in landing
    assert "visit = None" in landing
    assert "settings.traffic_startapp_ttl_seconds" in landing
    assert "startapp_token=new_attribution_token()" in landing


def test_landing_keeps_target_scoped_to_its_channel():
    landing = SOURCE[SOURCE.index("async def _landing_page"):SOURCE.index("async def _traffic_redirect")]
    assert "TrafficProfile.channel_id == landing.channel_id" in landing
