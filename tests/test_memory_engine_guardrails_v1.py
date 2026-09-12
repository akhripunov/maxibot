import pytest

from app.ads.autopilot import AUTONOMOUS, validate_action
from app.ads.memory import claim_key, maturity_for
from app.ads.maturity import SCALE, VALIDATE, evaluate_maturity, hypothesis_identity


def _enabled(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ads_autopilot_enabled", True)
    monkeypatch.setattr(settings, "ads_write_actions_enabled", True)


def test_memory_claim_identity_is_stable_and_scope_aware():
    assert claim_key("Creative B beats A", objective="TARGET_CPA", action="CREATIVE") == claim_key(" creative b beats a ", objective="target_cpa", action="creative")
    assert hypothesis_identity(scope="campaign-1", objective="TARGET_CPA", action="CREATIVE", dimension="creative", concept="locations") != hypothesis_identity(scope="campaign-2", objective="TARGET_CPA", action="CREATIVE", dimension="creative", concept="locations")


def test_inconclusive_does_not_advance_memory_maturity():
    assert maturity_for(outcome_status="INCONCLUSIVE", evidence_sufficient=False) == "EXPLORE"
    assert maturity_for(outcome_status="SUCCESS", evidence_sufficient=True, effect_size=0.10, min_effect=0.05, prior_successes=0) == VALIDATE
    assert maturity_for(outcome_status="SUCCESS", evidence_sufficient=True, effect_size=0.10, min_effect=0.05, prior_successes=1) == SCALE


def test_scale_requires_business_gates():
    result = evaluate_maturity(outcomes=[{"status": "SUCCESS", "evidence_sufficient": True, "effect_size": 0.10}], target_cpa=None, attribution_quality="UNKNOWN", data_health="HEALTHY", budget_available=True)
    assert result["state"] == VALIDATE
    assert result["gates"]["target_cpa"] is False


def test_guardrails_fail_closed_for_cap_stop_loss_and_evidence(monkeypatch):
    _enabled(monkeypatch)
    common = dict(execution_mode=AUTONOMOUS, action_type="CREATE_CREATIVE_TEST", data_trust="GOOD", active_experiments=0, config={"max_test_spend": 100, "allowed_actions": ["CREATE_CREATIVE_TEST"]})
    assert validate_action(**common, proposed_spend=101).reason == "MAX_TEST_SPEND"
    assert validate_action(**common, proposed_spend=10, experiment_spend=95, experiment_budget_cap=100).reason == "EXPERIMENT_BUDGET_CAP"
    assert validate_action(**common, evidence_sufficient=False).reason == "MINIMUM_EVIDENCE_NOT_MET"
    assert validate_action(**common, target_cpa=20, actual_cpa=40).reason == "TARGET_CPA_STOP_LOSS"


@pytest.mark.parametrize("reason,kwargs", [
    ("PROVIDER_NOT_READY", {"provider_ready": False}),
    ("ROLLBACK_NOT_AVAILABLE", {"rollback_available": False}),
    ("MAX_DURATION_EXPIRED", {"max_duration_expired": True}),
])
def test_guardrails_block_unsafe_lifecycle(monkeypatch, reason, kwargs):
    _enabled(monkeypatch)
    decision = validate_action(execution_mode=AUTONOMOUS, action_type="PAUSE_AD", data_trust="GOOD", active_experiments=0, config={"allowed_actions": ["PAUSE_AD"]}, **kwargs)
    assert decision.reason == reason
