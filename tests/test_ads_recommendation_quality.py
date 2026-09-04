from app.ads.analysis import (
    assess_recommendation_quality,
    build_recommendation_quality_layer,
    build_report_view,
    classify_signal_evolution,
)


def finding(**overrides):
    base = {
        "finding_id": "PLACEMENT_HIGH_CPA",
        "dimension": "PLACEMENT",
        "entity": "fixture-placement",
        "finding_type": "HIGH_CPA",
        "severity": "WATCH",
        "materiality": "HIGH",
        "sample_sufficiency": "SUFFICIENT",
        "confidence": "HIGH",
        "cpa_delta_pct": 0.82,
        "impact": 120.0,
        "evidence_ids": ["V2-fixture"],
    }
    base.update(overrides)
    return base


def test_01_sufficient_high_impact_signal_is_actionable():
    result = assess_recommendation_quality(finding())
    assert result["action_ladder"] == "CONSIDER_CHANGE"
    assert result["economic_impact"] == 120.0


def test_02_persistent_signal_can_be_strong_recommendation():
    result = assess_recommendation_quality(finding(), previous_recommendations=[{"status": "OPEN"}])
    assert result["action_ladder"] == "STRONG_RECOMMENDATION"
    assert result["signal_evolution"] == "PERSISTENT"


def test_03_weak_sample_does_not_become_strong():
    result = assess_recommendation_quality(finding(sample_sufficiency="WEAK_SIGNAL", confidence="MEDIUM"))
    assert result["action_ladder"] == "INVESTIGATE"


def test_04_unknown_is_not_zero():
    result = assess_recommendation_quality(finding(sample_sufficiency="INSUFFICIENT", impact=0))
    assert result["action_ladder"] == "NO_ACTION"
    assert result["sample_sufficiency"] == "INSUFFICIENT"


def test_05_no_material_signal_is_watch():
    result = assess_recommendation_quality(finding(materiality="LOW", severity="POSITIVE", cpa_delta_pct=0, impact=0))
    assert result["action_ladder"] == "WATCH"


def test_06_trend_is_persistence_evidence_not_second_finding():
    result = build_recommendation_quality_layer(
        {"positive": [finding()], "watch": [finding(finding_id="same", entity="fixture-placement")]},
        trends=[{"dimension": "PLACEMENT", "entity": "fixture-placement", "trend_state": "DEGRADING", "relative_delta": 0.9}],
    )
    assert len(result["secondary"]) == 0
    assert result["primary"]["quality"]["signal_evolution"] == "STRENGTHENING"


def test_07_max_one_primary_and_two_secondary():
    items = [finding(finding_id=str(i), entity=f"placement-{i}", impact=100 - i) for i in range(6)]
    result = build_recommendation_quality_layer({"positive": items, "watch": []})
    assert result["primary"]["finding_id"] == "0"
    assert len(result["secondary"]) == 2


def test_08_evidence_is_carried_without_invention():
    result = assess_recommendation_quality(finding(evidence_ids=["V2-fixture"]))
    assert result["evidence"] == ["V2-fixture"]


def test_09_reversible_and_low_risk_are_explicit():
    result = assess_recommendation_quality(finding())
    assert result["reversibility"] == "HIGH"
    assert result["risk"] == "LOW"


def test_10_freshness_is_preserved():
    assert assess_recommendation_quality(finding(), data_freshness_hours=2)["data_freshness_hours"] == 2


def test_11_resolved_trend_is_not_strengthening():
    assert classify_signal_evolution(finding(severity="POSITIVE"), [{"dimension": "PLACEMENT", "entity": "fixture-placement", "trend_state": "RECOVERED", "relative_delta": -0.3}]) == "RESOLVED"


def test_12_insufficient_fresh_data_has_wait_condition():
    result = assess_recommendation_quality(finding(sample_sufficiency="INSUFFICIENT"))
    assert result["signal_evolution"] == "INSUFFICIENT_NEW_DATA"
    assert result["wait_condition"]


def test_13_legacy_report_view_gets_quality_in_memory():
    view = build_report_view({"findings": {"positive": [finding()], "watch": []}, "campaign_summary": {"metrics": {}}, "trends": {"findings": []}})
    assert view["recommendation_quality"]["primary"]["finding_id"] == "PLACEMENT_HIGH_CPA"


def test_14_production_like_fixture_is_sanitized_and_not_hardcoded():
    fixture = {"findings": {"positive": [finding(entity="production-placement")], "watch": []}, "campaign_summary": {"metrics": {"cost": 935.74, "conversions": 48}}, "trends": {"findings": []}}
    quality = build_report_view(fixture)["recommendation_quality"]
    assert quality["primary"]["entity"] == "production-placement"
    assert "yandex" not in str(quality).lower()
