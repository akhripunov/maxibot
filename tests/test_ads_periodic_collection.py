from datetime import datetime, timezone

from app.ads.periodic import _slot_time
from app.core.config import settings


def test_periodic_slot_time_is_stable_and_round_in_moscow_time():
    now = datetime(2026, 9, 3, 7, 37, tzinfo=timezone.utc)
    first = _slot_time(now, "campaign-a", "CAMPAIGN")
    again = _slot_time(now, "campaign-a", "CAMPAIGN")
    assert first == again
    moscow = first.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow"))
    assert moscow.minute == 0
    assert moscow.second == 0
    assert moscow.microsecond == 0


def test_periodic_collection_is_fail_closed_by_default():
    assert settings.ads_periodic_collection_enabled is False
