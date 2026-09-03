from app.ads.analysis import bound_v2_dataset, build_deterministic_findings


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
