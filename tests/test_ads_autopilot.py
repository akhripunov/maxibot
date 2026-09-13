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
