import inspect

from app.ads.service import latest_view


def test_latest_view_prefers_ai_input_snapshot_over_new_granular_snapshot():
    source = inspect.getsource(latest_view)
    assert "ai.input_snapshot_id" in source
    assert 'source != "PERIODIC_GRANULAR"' in source
