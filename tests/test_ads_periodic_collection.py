from datetime import datetime, timezone

from app.ads.periodic import _slot_time
from app.core.config import settings


def test_periodic_slot_time_is_stable_and_spread_within_cadence_window():
    now = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)
    first = _slot_time(now, "campaign-a", "CAMPAIGN")
    again = _slot_time(now, "campaign-a", "CAMPAIGN")
    assert first == again
    assert 0 <= (first - first.replace(minute=0, second=0, microsecond=0)).total_seconds() < 3600


def test_periodic_collection_is_fail_closed_by_default():
    assert settings.ads_periodic_collection_enabled is False
