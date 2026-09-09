from datetime import date, timedelta

from app.ads.trends import build_trend_features


def _rows(values, *, dimension="CAMPAIGN", window="1d"):
    start = date(2026, 1, 1)
    return [{
        "dimension_type": dimension, "entity_id": "campaign-1", "dimension_value": "campaign",
        "window": window, "date_from": start + timedelta(days=index), "date_to": start + timedelta(days=index),
        "impressions": 100, "clicks": 10, "cost": value, "conversions": 1,
        "evidence_ids": [f"V2-{index:03d}"],
    } for index, value in enumerate(values)]


def test_trend_uses_completed_non_overlapping_daily_buckets_only():
    result = build_trend_features(_rows([20] * 14) + _rows([999], window="7d"), current_day=date(2026, 1, 15))
    findings = [item for item in result["findings"] if item["metric"] == "cpa" and item["period"] == "7d_vs_previous_7d"]
    assert findings and findings[0]["trend_state"] == "STABLE"
    assert result["history_days_available"] == 14


def test_cpa_direction_is_metric_aware():
    improving = build_trend_features(_rows([35, 34, 33, 32, 31, 30, 29, 28, 27, 26, 25, 24, 23, 22]), current_day=date(2026, 1, 15))
    degrading = build_trend_features(_rows([20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33]), current_day=date(2026, 1, 15))
    assert any(item["metric"] == "cpa" and item["trend_state"] == "IMPROVING" for item in improving["findings"])
    assert any(item["metric"] == "cpa" and item["trend_state"] == "DEGRADING" for item in degrading["findings"])


def test_missing_bucket_blocks_confirmed_trend():
    rows = _rows([20] * 14)
    rows.pop(5)
    result = build_trend_features(rows, current_day=date(2026, 1, 15))
    assert not any(item["period"] == "7d_vs_previous_7d" for item in result["findings"])
    assert result["coverage"]
    assert result["coverage"]["CAMPAIGN:campaign-1:campaign"]["missing_buckets"]
    assert result["history_health"] == "MISSING_BUCKETS"


def test_small_sample_does_not_create_strong_trend():
    rows = _rows([20] * 14)
    for row in rows:
        row["clicks"] = 1
    result = build_trend_features(rows, current_day=date(2026, 1, 15))
    assert result["findings"] == []


def test_current_partial_day_is_excluded_and_short_history_is_explicit():
    rows = _rows([20] * 7)
    rows.append({**_rows([999])[0], "date_from": date(2026, 1, 16), "date_to": date(2026, 1, 16)})
    result = build_trend_features(rows, current_day=date(2026, 1, 16))
    assert result["current_day_partial"] is True
    assert "2026-01-16" not in result["completed_days"]
    assert result["history_health"] == "HISTORY_SHORT"


def test_volatile_series_is_not_described_as_improving():
    result = build_trend_features(_rows([10, 45, 12, 51, 11, 48, 10, 49, 12, 50, 11, 47, 10, 46]), current_day=date(2026, 1, 15))
    assert any(item["metric"] == "cpa" and item["trend_state"] == "VOLATILE" for item in result["findings"])


def test_recovery_and_new_signal_states_are_supported():
    recovered = build_trend_features(_rows([30, 30, 30, 30, 30, 30, 30, 32, 32, 32, 24, 24, 25, 24]), current_day=date(2026, 1, 15))
    assert any(item["metric"] == "cpa" and item["trend_state"] == "RECOVERED" for item in recovered["findings"])
    new = build_trend_features(_rows([20]), current_day=date(2026, 1, 2))
    assert any(item["trend_state"] == "NEW_SIGNAL" for item in new["findings"])
