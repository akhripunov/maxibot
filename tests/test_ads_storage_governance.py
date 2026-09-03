import json
from datetime import datetime, timedelta, timezone

from app.ads.storage_governance import is_interval_observation
from app.db.models import AdsV2Observation
def test_rolling_windows_are_not_downsample_sources():
    row = AdsV2Observation(snapshot_id="s", window="7d", dimension_type="CAMPAIGN", provenance_json=json.dumps({"aggregation": "sum by Date"}), created_at=datetime.now(timezone.utc) - timedelta(days=61))
    assert is_interval_observation(row) is False
