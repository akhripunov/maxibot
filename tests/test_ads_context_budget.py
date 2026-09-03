import json

from app.ads.analysis import _compact_trends, bound_v2_dataset
from app.ads.context_budget import bound_ai_context


def _dataset(count=767):
    items = []
    for i in range(count):
        evidence_id = f"V2-{i:012d}"
        items.append({"value": f"placement-{i}", "cost": float(count-i), "evidence_id": evidence_id})
    return {
        "dataset_version": "test", "campaign_summary": {"windows": {"7d": {"cost": 1}}},
        "sections": {"placement": {"notable": items, "total_rows_analyzed": count}},
        "selection": {"placement": {"rows_available": count, "rows_included": count, "rows_excluded": 0}},
        "evidence": {item["evidence_id"]: {"metrics": item} for item in items},
    }


def test_767_placements_are_bounded_deterministically_without_mutating_input():
    original = _dataset()
    bounded = bound_v2_dataset(original, max_bytes=12000, max_evidence=30)
    again = bound_v2_dataset(original, max_bytes=12000, max_evidence=30)
    assert len(bounded["evidence"]) <= 30
    assert len(json.dumps(bounded, ensure_ascii=False).encode()) <= 12000
    assert bounded == again
    assert len(original["evidence"]) == 767


def test_selection_metadata_records_available_and_included_rows():
    bounded = bound_v2_dataset(_dataset(50), max_bytes=12000, max_evidence=10)
    metadata = bounded["selection"]["placement"]
    assert metadata["rows_available"] == 50
    assert metadata["rows_included"] == len(bounded["sections"]["placement"]["notable"])
    assert metadata["truncated"] is True


def test_compact_trends_prioritizes_business_metrics_and_drops_daily_series():
    trends = {
        "trend_engine_version": "trend_intelligence_v1",
        "history_days_available": 10,
        "completed_days": ["2026-08-24", "2026-09-02"],
        "current_day_partial": True,
        "history_health": "HISTORY_SHORT",
        "policy": "daily aggregates",
        "counts": {},
        "findings": [
            {"dimension": "AD", "entity": "a", "metric": "cost", "period": "1d", "trend_state": "VOLATILE", "evidence_ids": ["V2-a"]},
            {"dimension": "CAMPAIGN", "entity": "c", "metric": "cpa", "period": "3d_vs_previous_3d", "trend_state": "VOLATILE", "evidence_ids": ["V2-c"]},
        ],
    }
    compact = _compact_trends(trends, max_findings=1)
    assert compact["findings"][0]["metric"] == "cpa"
    assert "completed_days" not in compact
    assert compact["trend_7d_vs_previous_7d"] == "INSUFFICIENT_HISTORY"


def test_final_context_budget_trims_optional_rows_and_keeps_referenced_evidence():
    context = {
        "CURRENT_CAMPAIGN": {"id": "c", "name": "campaign"},
        "FACTS": {"TRENDS": {"findings": [{"evidence_ids": ["V2-trend"]}]}, "V2_ANALYSIS": {
            "sections": {"placement": {"notable": [
                {"value": "one", "evidence_id": "V2-one", "payload": "x" * 1000},
                {"value": "two", "evidence_id": "V2-two", "payload": "x" * 1000},
            ]}},
            "selection": {"placement": {"rows_available": 2}},
            "findings": {"positive": [], "watch": []},
            "evidence": {"V2-one": {}, "V2-two": {}, "V2-trend": {}},
        }},
        "RETRIEVED_PRIVATE_MEMORIES": [], "DATA_SUFFICIENCY": "SUFFICIENT",
        "MISSING_DATA": [], "ALLOWED_RECOMMENDATION_TYPES": [], "SYSTEM_CONSTRAINTS": {},
    }
    bounded, report = bound_ai_context(context, target_bytes=1800, max_bytes=3000, max_evidence=10)
    assert report["bytes"] <= 3000
    assert "V2-trend" in bounded["FACTS"]["V2_ANALYSIS"]["evidence"]
