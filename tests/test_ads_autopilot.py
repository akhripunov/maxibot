from pathlib import Path

import pytest

from app.ads.autopilot import (
    ADVISORY,
    AUTONOMOUS,
    AUTOPILOT_ACTION_ALLOWLIST,
    default_autopilot_config,
    validate_action,
    assess_provider_capabilities,
    store_provider_capability_assessment,
    stored_provider_capabilities,
    budget_experiment_readiness,
)


def test_autopilot_defaults_are_advisory_and_allowlisted():
    config = default_autopilot_config()
    assert config["monthly_budget_limit"] is None
    assert config["max_parallel_experiments"] == 1
    assert set(config["allowed_actions"]) == set(AUTOPILOT_ACTION_ALLOWLIST)
    assert validate_action(
        execution_mode=ADVISORY,
        action_type="PAUSE_AD",
        data_trust="GOOD",
        active_experiments=0,
    ).reason == "EXECUTION_MODE_ADVISORY"


def test_autopilot_fails_closed_when_global_writes_are_off():
    decision = validate_action(
        execution_mode=AUTONOMOUS,
        action_type="PAUSE_AD",
        data_trust="GOOD",
        active_experiments=0,
    )
    assert not decision.allowed
    assert decision.reason == "AUTOPILOT_GLOBAL_GUARD_DISABLED"


def test_experiment_budget_cap_is_independent_from_max_test_spend():
    """The broad promotion guardrail must not substitute for a per-test cap."""
    from app.core.config import settings
    original_autopilot = settings.ads_autopilot_enabled
    original_writes = settings.ads_write_actions_enabled
    settings.ads_autopilot_enabled = True
    settings.ads_write_actions_enabled = True
    try:
        allowed = validate_action(
            execution_mode=AUTONOMOUS,
            action_type="BOUNDED_BUDGET_REALLOCATION",
            data_trust="GOOD",
            active_experiments=0,
            config={"max_test_spend": 1000, "allowed_actions": ["BOUNDED_BUDGET_REALLOCATION"]},
            experiment_budget_cap=100,
            proposed_spend=100,
        )
        assert allowed.allowed
        denied = validate_action(
            execution_mode=AUTONOMOUS,
            action_type="BOUNDED_BUDGET_REALLOCATION",
            data_trust="GOOD",
            active_experiments=0,
            config={"max_test_spend": 1000, "allowed_actions": ["BOUNDED_BUDGET_REALLOCATION"]},
            experiment_budget_cap=100,
            experiment_spend=1,
            proposed_spend=100,
        )
        assert denied.reason == "EXPERIMENT_BUDGET_CAP"
    finally:
        settings.ads_autopilot_enabled = original_autopilot
        settings.ads_write_actions_enabled = original_writes


def test_missing_experiment_budget_cap_fails_closed_even_below_max_test_spend():
    from app.core.config import settings
    original_autopilot = settings.ads_autopilot_enabled
    original_writes = settings.ads_write_actions_enabled
    settings.ads_autopilot_enabled = True
    settings.ads_write_actions_enabled = True
    try:
        decision = validate_action(
            execution_mode=AUTONOMOUS,
            action_type="BOUNDED_BUDGET_REALLOCATION",
            data_trust="GOOD",
            active_experiments=0,
            config={"max_test_spend": 1000, "allowed_actions": ["BOUNDED_BUDGET_REALLOCATION"]},
            proposed_spend=100,
        )
        assert not decision.allowed
        assert decision.reason == "EXPERIMENT_BUDGET_CAP_MISSING"
    finally:
        settings.ads_autopilot_enabled = original_autopilot
        settings.ads_write_actions_enabled = original_writes


def test_experiment_budget_cap_migration_is_additive_and_has_no_ads_table():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "alembic/versions/0097_experiment_budget_cap_v1.py").read_text()
    assert 'down_revision = "0096_experience_transfer_prior_engine_v1"' in migration
    assert 'op.add_column("ads_promotion_campaign_links"' in migration
    assert '"experiment_budget_cap"' in migration
    assert "op.create_table" not in migration


@pytest.mark.parametrize("action", ["CREATE_CAMPAIGN", "CHANGE_BID", "PAUSE_CAMPAIGN"])
def test_future_provider_actions_are_not_v1_allowlisted(action, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ads_autopilot_enabled", True)
    monkeypatch.setattr(settings, "ads_write_actions_enabled", True)
    decision = validate_action(
        execution_mode=AUTONOMOUS,
        action_type=action,
        data_trust="GOOD",
        active_experiments=0,
        config={"allowed_actions": [action]},
    )
    assert not decision.allowed
    assert decision.reason == "ACTION_NOT_ALLOWLISTED"


def test_autopilot_migration_adds_columns_without_new_table():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "alembic/versions/0086_ads_autopilot_v1.py").read_text()
    assert "ads_promotion_campaign_links" in migration
    assert "execution_mode" in migration
    assert "autopilot_config_json" in migration
    assert "execution_updated_at" in migration
    assert "op.create_table" not in migration
    experiment_migration = (root / "alembic/versions/0087_canonical_experiment_identity.py").read_text()
    assert "experiment_id" in experiment_migration
    assert "experiment_seq" in experiment_migration
    assert "experiment_status" in experiment_migration
    assert "op.create_table" not in experiment_migration


def test_autopilot_confirmation_is_human_readable_and_hides_internal_policy_names():
    source = (Path(__file__).resolve().parents[1] / "app/bot/menu.py").read_text()
    section = source[source.index('if action_name == "ads_autopilot_enable_confirm":'):source.index('if action_name == "ads_autopilot_enable":', source.index('if action_name == "ads_autopilot_enable_confirm":'))]
    for forbidden in ("ID: {link.campaign_id}", "V1 allowlist", "EXCLUDE_PLACEMENT"):
        assert forbidden not in section
    assert "Включение автопилота" in section
    assert "остальные кампании остаются только для анализа" in section


def test_autopilot_confirmation_fails_closed_without_confirmed_provider_state():
    source = (Path(__file__).resolve().parents[1] / "app/bot/menu.py").read_text()
    section = source[source.index("provider_ready = provider_state == \"ON\""):source.index("if action_name == \"ads_autopilot_enable\":", source.index("provider_ready = provider_state == \"ON\""))]
    assert "Не удалось подтвердить состояние" in section
    assert "if eligible:" in section
    assert "Включить автопилот" in section


def test_unknown_provider_preflight_offers_retry_and_details_instead_of_fix_settings():
    source = (Path(__file__).resolve().parents[1] / "app/bot/menu.py").read_text()
    section = source[source.index('if action_name == "ads_autopilot_enable_confirm":'):source.index('if action_name == "ads_autopilot_enable":', source.index('if action_name == "ads_autopilot_enable_confirm":'))]
    assert "↻ Проверить ещё раз" in section
    assert "🔍 Технические детали" in section
    assert "ads_autopilot_preflight_retry:" in section
    assert "ads_autopilot_preflight_details:" in section
    unknown_controls = section[section.index("elif not provider_ready:"):section.index("else:", section.index("elif not provider_ready:"))]
    assert "🔧 Исправить настройку" not in unknown_controls


def test_confirmed_inactive_provider_state_restores_fix_settings_continuation():
    source = (Path(__file__).resolve().parents[1] / "app/bot/menu.py").read_text()
    section = source[source.index('if action_name == "ads_autopilot_enable_confirm":'):source.index('if action_name == "ads_autopilot_enable":', source.index('if action_name == "ads_autopilot_enable_confirm":'))]
    inactive_branch = section[section.index("elif provider_state in _AUTOPILOT_INACTIVE_PROVIDER_STATES:"):section.index("elif not provider_ready:", section.index("elif provider_state in _AUTOPILOT_INACTIVE_PROVIDER_STATES:"))]
    assert '"🔧 Исправить настройку"' in inactive_branch
    assert '"↻ Проверить ещё раз"' not in inactive_branch


def test_preflight_retry_is_read_only_and_returns_to_confirmation_renderer():
    source = (Path(__file__).resolve().parents[1] / "app/bot/runtime.py").read_text()
    assert 'action.startswith(("ads_autopilot_preflight_retry:", "ads_autopilot_preflight_details:"))' in source
    assert 'await self._render(user_id, f"{action}")' in source
    menu_source = (Path(__file__).resolve().parents[1] / "app/bot/menu.py").read_text()
    assert "The provider read below is always fresh and read-only." in menu_source


def test_unknown_campaign_state_can_have_partial_child_capabilities_without_budget_access():
    assessment = assess_provider_capabilities(
        campaign_id="713637226",
        campaign_state="UNKNOWN",
        adgroups=[{"Id": 7, "CampaignId": "713637226"}],
        ads=[{"Id": 11, "AdGroupId": 7, "CampaignId": "713637226", "State": "ON"}],
        budget_state_visible=False,
    )
    assert assessment["provider_manageability"] == "PARTIAL"
    assert set(assessment["verified_capabilities"]) == {"CREATE_AD_TEST", "CREATE_CREATIVE_TEST", "ENABLE_AD", "PAUSE_AD"}
    assert "BOUNDED_BUDGET_REALLOCATION" not in assessment["verified_capabilities"]


def test_suspended_campaign_can_attest_campaign_level_budget_without_child_objects():
    assessment = assess_provider_capabilities(
        campaign_id="714373352",
        campaign_state="SUSPENDED",
        budget_state_visible=True,
    )
    assert assessment["verified_capabilities"] == ["BOUNDED_BUDGET_REALLOCATION"]


def test_budget_readiness_does_not_inherit_two_week_trend_gate():
    result = budget_experiment_readiness(
        data_trust="GOOD", direct_auth=True, business_attribution=True,
        attribution_coverage=0.9, freshness_hours=2, clicks=12,
        exact_outcomes=3, spend=50, budget_capability_verified=True,
        baseline_ready=True, stop_loss_ready=True,
        one_active_experiment_guard=True, trend_windows_ready=False,
    )
    assert result["ready"] is True
    assert result["requires_14_days"] is False


def test_budget_readiness_fails_closed_for_stale_unknown_and_small_samples():
    stale = budget_experiment_readiness(
        data_trust="GOOD", direct_auth=True, business_attribution=True,
        attribution_coverage=0.9, freshness_hours=49, clicks=12,
        exact_outcomes=3, spend=50, budget_capability_verified=True,
        baseline_ready=True, stop_loss_ready=True, one_active_experiment_guard=True,
    )
    unknown = budget_experiment_readiness(
        data_trust="GOOD", direct_auth=True, business_attribution=True,
        attribution_coverage=0.9, freshness_hours=None, clicks=12,
        exact_outcomes=3, spend=50, budget_capability_verified=True,
        baseline_ready=True, stop_loss_ready=True, one_active_experiment_guard=True,
    )
    small = budget_experiment_readiness(
        data_trust="GOOD", direct_auth=True, business_attribution=True,
        attribution_coverage=0.9, freshness_hours=2, clicks=9,
        exact_outcomes=2, spend=0, budget_capability_verified=True,
        baseline_ready=True, stop_loss_ready=True, one_active_experiment_guard=True,
    )
    assert stale["ready"] is False and "FRESHNESS_UNKNOWN_OR_OVER_48H" in stale["blockers"]
    assert unknown["ready"] is False and "FRESHNESS_UNKNOWN_OR_OVER_48H" in unknown["blockers"]
    assert small["ready"] is False and {"MIN_CLICKS_NOT_MET", "MIN_EXACT_OUTCOMES_NOT_MET", "MIN_SPEND_NOT_MET"}.issubset(small["blockers"])


def test_child_capability_scope_rejects_cross_campaign_objects():
    assessment = assess_provider_capabilities(
        campaign_id="713637226",
        campaign_state="UNKNOWN",
        adgroups=[{"Id": 7, "CampaignId": "999"}],
        ads=[{"Id": 11, "AdGroupId": 7, "CampaignId": "999", "State": "ON"}],
    )
    assert assessment["provider_manageability"] == "NONE"
    assert assessment["verified_capabilities"] == []


def test_control_plane_denies_unverified_capability(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ads_autopilot_enabled", True)
    monkeypatch.setattr(settings, "ads_write_actions_enabled", True)
    decision = validate_action(
        execution_mode=AUTONOMOUS,
        action_type="BOUNDED_BUDGET_REALLOCATION",
        data_trust="GOOD",
        active_experiments=0,
        verified_provider_capabilities={"PAUSE_AD", "ENABLE_AD"},
    )
    assert not decision.allowed
    assert decision.reason == "PROVIDER_CAPABILITY_NOT_VERIFIED"


def test_provider_capability_assessment_uses_existing_campaign_metadata():
    from app.db.models import AdsCampaign
    campaign = AdsCampaign(account_id="account", external_id="713637226", name="report-only")
    assessment = assess_provider_capabilities(campaign_id="713637226", campaign_state="UNKNOWN")
    store_provider_capability_assessment(campaign, assessment)
    stored = stored_provider_capabilities(campaign)
    assert stored["provider_manageability"] == "NONE"
    assert stored["verified_capabilities"] == []
    assert "provider_capability_assessment" in campaign.provider_metadata_json
