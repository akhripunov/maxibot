from datetime import date
from types import SimpleNamespace

from app.ads.directologist import (
    attribution_quality,
    classify_trend,
    comparison_windows,
    comparative_metrics,
    confidence,
    root_cause_status,
)
from app.ads.directologist import _channel_campaign_match


def test_windows_exclude_partial_today_and_use_equal_seven_day_periods():
    windows = comparison_windows(today=date(2026, 9, 7))
    assert windows["current"] == {"start": "2026-08-31", "end": "2026-09-06", "days": 7}
    assert windows["baseline"] == {"start": "2026-08-24", "end": "2026-08-30", "days": 7}
    assert windows["partial_day_guard"] == "PASS"


def test_comparative_metrics_only_sum_campaign_dimension():
    rows = [
        SimpleNamespace(aggregate_date=date(2026, 8, 31), dimension_type="CAMPAIGN", impressions=100, clicks=10, cost=20, conversions=2),
        SimpleNamespace(aggregate_date=date(2026, 8, 31), dimension_type="AD", impressions=900, clicks=90, cost=200, conversions=20),
    ]
    windows = comparison_windows(today=date(2026, 9, 7))
    result = comparative_metrics(rows, windows)
    assert result["current"]["impressions"] == 100
    assert result["current"]["clicks"] == 10
    assert result["status"] == "INSUFFICIENT_DATA"


def test_attribution_quality_never_upgrades_unresolved_or_unattributed():
    assert attribution_quality(has_yclid=True, campaign_match=True, deterministic_link=True) == "MATCHED"
    assert attribution_quality(has_yclid=True, campaign_match=None, deterministic_link=True) == "OBSERVED"
    assert attribution_quality(has_yclid=True, campaign_match=False, deterministic_link=True) == "UNRESOLVED"
    assert attribution_quality(has_yclid=False, campaign_match=None, deterministic_link=False) == "UNATTRIBUTED"


def test_channel_matching_accepts_suffixed_utm_campaign_and_keeps_wrong_campaign_unresolved():
    visit = SimpleNamespace(
        utm_campaign="msk365_rsya_msk_click_test1.713798021.5791778274",
        campaign_metadata_json=None,
    )
    assert _channel_campaign_match(visit, "msk365_rsya_msk_click_test1") is True
    assert _channel_campaign_match(visit, "other-campaign") is False


def test_confirmed_channel_summary_uses_utm_matcher_for_single_campaign():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/ads/directologist.py").read_text()
    assert "_channel_campaign_match(visit, campaign.external_id)" in source
    assert 'TrafficMembership.attribution_status.in_(("EXACT", "HIGH"))' in source


def test_trend_and_confidence_fail_closed_for_small_samples():
    assert classify_trend(current=10, baseline=5, metric="provider_cpa", clicks=9, confirmed_outcomes=4) == "INSUFFICIENT_DATA"
    result = confidence(clicks=9, confirmed_outcomes=1, freshness_hours=None, attribution_coverage=None, root_cause_proven=False, windows_ready=False)
    assert result["level"] == "INSUFFICIENT"
    assert result["reasons"]


def test_root_cause_is_not_invented_without_sufficient_evidence():
    result = root_cause_status(contributors=[{"impact": 100, "evidence_ids": ["V2-1"]}], sufficient=False)
    assert result["status"] == "ROOT_CAUSE_NOT_PROVEN"
    assert result["top_contributors"] == []
