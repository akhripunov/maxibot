from app.ads.analysis import _metrics, _confidence


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
