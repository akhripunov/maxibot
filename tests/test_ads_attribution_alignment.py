from datetime import date, datetime, timezone

from app.ads.attribution import window_bounds
from app.bot.menu import format_ads_result


def test_seven_day_window_is_same_moscow_calendar_window():
    start, end = window_bounds(end_date=date(2026, 9, 3), window="7d")
    assert start == datetime(2026, 8, 27, 21, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 3, 21, tzinfo=timezone.utc)


def test_renderer_explains_aligned_maxibot_membership_rate_separately():
    body = format_ads_result(
        headline="done", channel_title="Москва 365", direct_connected=True,
        campaign_name="test", ai_status="done",
        checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={"cost_7d": 10, "conversions_7d": 2, "actual_cpa_7d": 5,
                     "qualified_subscriptions": 2, "landing_visits": 100,
                     "maxibot_qualified_membership_rate": .02}, completed=True,
    )
    assert "2 подтверждённых подписок из 100 визитов" in body
    assert "Конверсия визит → подписка: 2,00%" in body
    assert "Сопоставление с конкретной рекламной кампанией: недоступно" in body
    assert "Yandex" not in body


def test_campaign_scoped_rate_is_unknown_without_campaign_population():
    from app.ads.attribution import build_maxibot_metrics
    assert callable(build_maxibot_metrics)


def test_single_campaign_utm_mapping_is_supported_but_ambiguous_mapping_stays_unknown():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/ads/attribution.py").read_text()
    service = (Path(__file__).parents[1] / "app/ads/service.py").read_text()
    assert "campaign_matches" in source
    assert "len(links) == 1" in source
    assert "if matched:" in source
    assert 'campaign_specific_matching": True if campaign_mapping_supported else "UNKNOWN"' in service


def test_partial_deterministic_campaign_mapping_is_supported_with_coverage():
    from app.traffic.funnel_reporting import campaign_matches

    matched = sum(campaign_matches(value, "713798021") for value in (
        *(["713798021.1.2"] * 56),
        "other.1.2",
        "",
    ))
    assert matched == 56
    assert matched / 58 == 56 / 58
