from app.ads.analysis import (
    assess_recommendation_quality,
    build_recommendation_quality_layer,
    build_quality_decision_snapshot,
    build_report_view,
    classify_signal_evolution,
)
from app.bot.menu import format_ads_result


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


def test_15_snapshot_is_bounded_and_has_primary_trace_fields():
    dataset = {"findings": {"positive": [finding()], "watch": []}, "recommendation_quality": build_recommendation_quality_layer({"positive": [finding()], "watch": []})}
    snapshot = build_quality_decision_snapshot(dataset)
    candidate = snapshot["primary_candidate"]
    assert snapshot["version"] == "quality_decision_snapshot_v1"
    assert candidate["safe_entity"] == "fixture-placement"
    assert candidate["WHY_THIS"] and candidate["NEXT_STEP"]
    assert "sections" not in str(snapshot)


def test_16_snapshot_uses_the_same_quality_profile_not_a_second_score():
    quality = build_recommendation_quality_layer({"positive": [finding()], "watch": []}, previous_recommendations=[{"status": "OPEN"}])
    snapshot = build_quality_decision_snapshot({"findings": {"positive": [finding()], "watch": []}}, quality=quality)
    assert snapshot["primary_candidate"]["action_level"] == quality["primary"]["quality"]["action_ladder"]
    assert snapshot["primary_candidate"]["signal_evolution"] == quality["primary"]["quality"]["signal_evolution"]


def test_17_snapshot_preserves_unknown_without_coercing_to_zero():
    item = finding(sample_sufficiency="INSUFFICIENT", impact=0, confidence="UNKNOWN")
    quality = build_recommendation_quality_layer({"positive": [item], "watch": []})
    snapshot = build_quality_decision_snapshot({"findings": {"positive": [item], "watch": []}}, quality=quality)
    assert snapshot["primary_candidate"]["data_sufficiency"] == "INSUFFICIENT"
    assert snapshot["primary_candidate"]["confidence"] == "UNKNOWN"


def test_18_pending_outcome_is_not_rendered_as_completed():
    quality = build_recommendation_quality_layer({"positive": [finding()], "watch": []}, measured_outcomes=[{"status": "PENDING"}])
    snapshot = build_quality_decision_snapshot({"findings": {"positive": [finding()], "watch": []}}, quality=quality)
    body = format_ads_result(headline="done", channel_title="Канал", direct_connected=True, campaign_name="campaign", ai_status="done", checked_at=None, fact_values={}, completed=True, v2_analysis={"QUALITY_DECISION_SNAPSHOT": snapshot})
    assert "Измерение результата ещё ожидается" in body
    assert "Измеренный результат доступен" not in body


def test_19_history_state_is_only_shown_when_known():
    unknown = build_quality_decision_snapshot({"findings": {"positive": [finding()], "watch": []}})
    assert unknown["history_state"] == "UNKNOWN"
    known = build_quality_decision_snapshot({"findings": {"positive": [finding()], "watch": []}}, history_state="AVAILABLE")
    assert known["history_state"] == "AVAILABLE"


def test_20_wait_condition_is_omitted_when_not_known():
    quality = build_recommendation_quality_layer({"positive": [finding()], "watch": []})
    snapshot = build_quality_decision_snapshot({"findings": {"positive": [finding()], "watch": []}}, quality=quality)
    assert snapshot["primary_candidate"]["WAIT_CONDITION"] is None


def test_21_renderer_humanizes_action_level_and_keeps_safe_label():
    item = finding()
    quality = build_recommendation_quality_layer({"positive": [item], "watch": []})
    snapshot = build_quality_decision_snapshot({"findings": {"positive": [item], "watch": []}}, quality=quality)
    body = format_ads_result(headline="done", channel_title="Канал", direct_connected=True, campaign_name="campaign", ai_status="done", checked_at=None, fact_values={}, completed=True, v2_analysis={"QUALITY_DECISION_SNAPSHOT": snapshot})
    assert "Можно рассмотреть изменение" in body
    assert "CONSIDER_CHANGE" not in body


def test_22_legacy_renderer_without_snapshot_remains_safe():
    body = format_ads_result(headline="done", channel_title="Канал", direct_connected=True, campaign_name="campaign", ai_status="done", checked_at=None, fact_values={}, completed=True, v2_analysis={})
    assert "Решение:" not in body


def test_23_snapshot_history_and_outcome_states_are_profile_metadata():
    quality = build_recommendation_quality_layer({"positive": [finding()], "watch": []}, previous_recommendations=[{"status": "OPEN"}], measured_outcomes=[{"status": "PENDING"}])
    snapshot = build_quality_decision_snapshot({"findings": {"positive": [finding()], "watch": []}}, quality=quality)
    assert snapshot["history_state"] == "AVAILABLE"
    assert snapshot["outcome_state"] == "PENDING"


def test_24_snapshot_does_not_contain_raw_ai_or_ads_dataset():
    snapshot = build_quality_decision_snapshot({"findings": {"positive": [finding()], "watch": []}})
    assert "raw_structured_output_json" not in str(snapshot)
    assert "daily_rows" not in str(snapshot)
