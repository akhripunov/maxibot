from datetime import date, timedelta
from types import SimpleNamespace

from app.ads.analysis import _metrics, _confidence, campaign_fact_set_from_v2, campaign_metrics_from_daily_aggregates


def test_metrics_use_null_for_zero_denominators_and_keep_campaign_shares():
    row = _metrics({"impressions": 0, "clicks": 0, "cost": 10, "conversions": 0}, campaign={"impressions": 100, "clicks": 20, "cost": 100, "conversions": 4}, history_days=1)
    assert row["ctr"] is None
    assert row["cpc"] is None
    assert row["cvr"] is None
    assert row["cpa"] is None
    assert row["cost_share"] == 0.1
    assert row["conversion_share"] == 0


def test_confidence_rejects_micro_sample_and_accepts_strong_sample():
    assert _confidence({"impressions": 3, "clicks": 1, "cost": 2, "conversions": 0}, history_days=1) == "LOW"
    assert _confidence({"impressions": 300, "clicks": 30, "cost": 20, "conversions": 5}, history_days=7) == "HIGH"


def test_ai_flow_uses_v2_dataset_and_does_not_recollect_reports():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "ads" / "service.py").read_text()
    assert "build_v2_analysis_dataset" in source
    assert '"V2_ANALYSIS": dataset' in source
    assert "client.get_campaign_report" not in source


def test_campaign_weekly_metrics_use_seven_completed_days_and_exclude_current_day():
    rows = [
        SimpleNamespace(
            aggregate_date=date(2026, 9, 28) + timedelta(days=offset),
            dimension_type="CAMPAIGN",
            impressions=100,
            clicks=10,
            cost=20,
            conversions=2,
        )
        for offset in range(7)
    ]
    rows.append(SimpleNamespace(
        aggregate_date=date(2026, 10, 5), dimension_type="CAMPAIGN",
        impressions=9999, clicks=999, cost=999, conversions=99,
    ))
    rows.append(SimpleNamespace(
        aggregate_date=date(2026, 10, 4), dimension_type="CAMPAIGN", provider="TELEGRAM_ADS",
        impressions=8888, clicks=888, cost=888, conversions=88,
    ))

    result = campaign_metrics_from_daily_aggregates(rows, period_to=date(2026, 10, 4), days=7)

    assert result["period"] == {"from": "2026-09-28", "to": "2026-10-04", "timezone": "Europe/Moscow"}
    assert result["impressions"] == 700
    assert result["clicks"] == 70
    assert result["cost"] == 140
    assert result["conversions"] == 14
    assert result["days_present"] == 7
    assert result["expected_days"] == 7
    assert result["complete"] is True


def test_campaign_window_reports_missing_days_without_manufacturing_zero_rows():
    rows = [
        SimpleNamespace(aggregate_date=date(2026, 9, 28), dimension_type="CAMPAIGN", impressions=100, clicks=10, cost=20, conversions=2),
        SimpleNamespace(aggregate_date=date(2026, 10, 4), dimension_type="CAMPAIGN", impressions=50, clicks=5, cost=10, conversions=1),
    ]
    result = campaign_metrics_from_daily_aggregates(rows, period_to=date(2026, 10, 4), days=7)
    assert result["impressions"] == 150
    assert result["days_present"] == 2
    assert result["expected_days"] == 7
    assert result["complete"] is False


def test_direct_conversions_and_maxibot_campaign_memberships_remain_separate_facts():
    dataset = {
        "period": {"to": "2026-10-04"},
        "campaign_summary": {"windows": {"7d": {
            "impressions": 1000, "clicks": 40, "cost": 940, "conversions": 58,
        }}},
    }
    fact_set = campaign_fact_set_from_v2(dataset, traffic={
        "visits": 80, "to_channel": 75, "new_members": 51,
        "EXACT": 51, "HIGH": 0,
    })
    assert fact_set.conversions_7d == 58
    assert fact_set.subscriptions == 51
    assert fact_set.qualified_subscriptions == 51
