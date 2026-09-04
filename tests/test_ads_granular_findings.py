from app.ads.analysis import bound_v2_dataset, build_deterministic_findings, build_report_view, dedupe_trend_findings


def _dataset(rows):
    return {
        "campaign_summary": {"metrics": {"impressions": 1000, "clicks": 100, "cost": 100, "conversions": 10}},
        "sections": {"placement": {"notable": rows}},
    }


def test_sufficient_expensive_placement_is_watch_not_execution():
    result = build_deterministic_findings(_dataset([{
        "value": "expensive.example", "evidence_id": "V2-expensive", "impressions": 300,
        "clicks": 30, "cost": 30, "conversions": 1, "ctr": .1, "cpc": 1,
        "cvr": .033, "cpa": 30, "cost_share": .30, "sample_sufficiency": "SUFFICIENT",
        "cpa_delta_pct": 2.0,
    }]))
    assert result["watch"][0]["finding_id"] == "PLACEMENT_HIGH_CPA"
    assert "no automatic exclusion" in result["policy"]


def test_low_sample_never_becomes_strong_finding():
    result = build_deterministic_findings(_dataset([{
        "value": "tiny.example", "evidence_id": "V2-tiny", "cost": 50,
        "sample_sufficiency": "WEAK_SIGNAL", "cpa_delta_pct": 3.0,
    }]))
    assert result["positive"] == []
    assert result["watch"]
    assert result["watch"][0]["sample_sufficiency"] == "WEAK_SIGNAL"


def test_bounded_dataset_keeps_findings_evidence_registry_consistent():
    dataset = _dataset([{
        "value": "example", "evidence_id": "V2-example", "impressions": 100,
        "clicks": 10, "cost": 20, "conversions": 2, "sample_sufficiency": "WEAK_SIGNAL",
        "cpa": 10, "cpa_delta_pct": 1.0, "cost_share": .2,
    }])
    dataset["evidence"] = {"V2-example": {"metrics": dataset["sections"]["placement"]["notable"][0]}}
    dataset["findings"] = build_deterministic_findings(dataset)
    bounded = bound_v2_dataset(dataset, max_bytes=2000, max_evidence=10)
    allowed = set(bounded["evidence"])
    for group in ("positive", "watch"):
        assert all(ref in allowed for item in bounded["findings"][group] for ref in item["evidence_ids"])


def test_legacy_report_view_fallback_preserves_grounded_placement_and_single_ad():
    dataset = {
        "campaign_summary": {"metrics": {"cpa": 20.0}},
        "sections": {"ad": {"total_rows_analyzed": 1, "notable": [{"value": "713798021"}]},
                     "ad_group": {"total_rows_analyzed": 1, "notable": [{"value": "g1"}]},
                     "placement": {"total_rows_analyzed": 371, "notable": [{"value": "yandex.ru"}]}},
        "selection": {"ad": {"rows_available": 1, "rows_included": 1}, "ad_group": {"rows_available": 1, "rows_included": 1},
                      "placement": {"rows_available": 371, "rows_included": 26}},
        "findings": {"positive": [], "watch": [{"dimension": "PLACEMENT", "entity": "yandex.ru", "metrics": {"cpa": 35.06, "cost_share": .3088, "sample_sufficiency": "SUFFICIENT"}, "materiality": "HIGH", "sample_sufficiency": "SUFFICIENT", "evidence_ids": ["V2-y"]}]},
        "trends": {"findings": []}, "evidence": {"V2-y": {"source": "ADS_V2_OBSERVATION"}},
    }
    view = build_report_view(dataset)
    assert view["primary_findings"][0]["label"] == "yandex.ru"
    assert view["primary_findings"][0]["campaign_cpa"] == 20.0
    assert view["creative_comparison"] == "SINGLE_ENTITY"
    assert view["comparison"]["placement"]["available"] == 371


def test_report_view_fallback_keeps_unknown_counts_unknown_and_native_view_unchanged():
    legacy = build_report_view({"findings": {}, "sections": {"placement": {"notable": []}}})
    assert legacy["comparison"]["placement"]["state"] == "UNKNOWN"
    native = {"comparison": {"ad": {"state": "SINGLE_ENTITY"}}, "primary_findings": [{"label": "kept"}]}
    assert build_report_view({"report_view": native}) is native


def test_trend_dedup_collapses_equivalent_hierarchy_but_keeps_distinct_child():
    base = [{"dimension": "CAMPAIGN", "entity": "c", "entity_id": "c", "metric": "cpa", "period": "3d", "trend_state": "VOLATILE", "relative_delta": .2},
            {"dimension": "AD_GROUP", "entity": "g", "entity_id": "g", "metric": "cpa", "period": "3d", "trend_state": "VOLATILE", "relative_delta": .2},
            {"dimension": "AD", "entity": "a", "entity_id": "a", "metric": "cpa", "period": "3d", "trend_state": "VOLATILE", "relative_delta": .2}]
    assert len(dedupe_trend_findings(base)) == 1
    distinct = base[:-1] + [{**base[-1], "relative_delta": .4}]
    assert len(dedupe_trend_findings(distinct)) == 2
