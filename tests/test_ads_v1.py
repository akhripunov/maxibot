from pathlib import Path
from datetime import datetime, timezone

from app.ads.facts import build_campaign_facts
from app.ads.health import data_health, sufficiency
from app.ads.llm import assert_safe_context, validate_output


ROOT = Path(__file__).resolve().parents[1]
MENU = (ROOT / "app" / "bot" / "menu.py").read_text()
RUNTIME = (ROOT / "app" / "bot" / "runtime.py").read_text()
DIRECT = (ROOT / "app" / "integrations" / "yandex_direct.py").read_text()
MIGRATION = (ROOT / "alembic" / "versions" / "0046_ads_v1_foundation.py").read_text()


def test_ads_v1_is_hidden_behind_server_side_gate_and_positioned_after_direct():
    assert "can_access_ads(admin.external_id)" in MENU
    assert 'controls = [(yandex_button' in MENU
    assert 'controls.append(("🤖 AI Продвижение"' in MENU
    assert '("📊 Аналитика"' in MENU
    assert "can_access_ads(str(user_id))" in RUNTIME


def test_fact_engine_keeps_unknown_distinct_from_zero_and_calculates_metrics():
    facts = build_campaign_facts([{"Date": "2026-09-02", "Impressions": "100", "Clicks": "10", "Cost": "50", "Conversions": "2"}], campaign_start="2026-09-01", traffic={"visits": 10, "to_channel": 7, "new_members": 3, "EXACT": 1, "HIGH": 1}, now=datetime(2026, 9, 3, tzinfo=timezone.utc))
    assert facts.ctr_7d == 0.1
    assert facts.actual_cpa_7d == 25
    assert facts.impressions_change_3d_vs_previous_3d is None
    assert "FACT-001" in facts.evidence()


def test_sufficiency_health_and_schema_fail_closed():
    assert sufficiency(mapped_campaigns=0, report_days=0, conversions=None, attribution_available=False, direct_available=False)[0] == "INSUFFICIENT"
    health = data_health(direct_available=False, mapped_campaigns=0, attribution_available=False, snapshot_fresh=False, ai_available=False, memory_db_available=True)
    assert health["status"] == "UNHEALTHY"
    assert validate_output({"status": "OK"}, {"FACT-001"})[0] is None


def test_valid_schema_accepts_only_real_fact_evidence_and_memory_provenance():
    value = {
        "status": "OK", "diagnosis_code": "OBSERVE", "summary": "Данных достаточно для наблюдения.",
        "confidence": "LOW", "data_sufficiency": "PARTIAL",
        "evidence": [{"evidence_id": "FACT-001", "statement": "Есть фактические данные"}],
        "recommendation": {"action_code": "OBSERVE", "current_value": None, "suggested_value": None, "reason": "Без изменений"},
        "do_not_change": [], "missing_data": [],
        "memory_candidates": [{"statement": "Нужна дополнительная выборка", "source_evidence_ids": ["FACT-001"]}],
    }
    assert validate_output(value, {"FACT-001"})[0] == value
    value["memory_candidates"][0]["source_evidence_ids"] = ["FACT-999"]
    assert validate_output(value, {"FACT-001"})[0] is None


def test_safe_context_secret_scan_fails_closed():
    assert_safe_context({"FACTS": {"FACT-001": 1}})
    import pytest
    with pytest.raises(ValueError, match="SAFE_CONTEXT_SECRET_SCAN_FAILED"):
        assert_safe_context({"oauth_token": "hidden"})


def test_ads_schema_is_isolated_and_direct_client_stays_read_only():
    for table in ("ads_accounts", "ads_campaigns", "ads_snapshots", "ads_sync_runs", "ads_ai_runs", "ads_memories", "ads_data_health", "ads_actions", "ads_experiments"):
        assert table in MIGRATION
    assert '"method": "get"' in DIRECT
    assert "def update" not in DIRECT.lower()
    assert "def create" not in DIRECT.lower()
    assert "Campaigns.update" not in (ROOT / "app" / "ads" / "service.py").read_text()
