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
    set_mode,
    campaign_state_allows_execution,
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
    section = source[source.index("provider_known = provider_state in {\"ON\", \"SUSPENDED\"}"):source.index("if action_name == \"ads_autopilot_enable\":", source.index("provider_known = provider_state in {\"ON\", \"SUSPENDED\"}"))]
    assert 'provider_state = str(provider_state or "UNKNOWN").upper()' in source
    assert "Не удалось подтвердить состояние" in section
    assert "if eligible:" in section
    assert "Включить автопилот" in section
    assert 'provider_state == "SUSPENDED"' in section
    assert "выполнение действий начнётся только после запуска кампании" in section


def test_unknown_provider_preflight_offers_retry_and_details_instead_of_fix_settings():
    source = (Path(__file__).resolve().parents[1] / "app/bot/menu.py").read_text()
    section = source[source.index('if action_name == "ads_autopilot_enable_confirm":'):source.index('if action_name == "ads_autopilot_enable":', source.index('if action_name == "ads_autopilot_enable_confirm":'))]
    assert "↻ Проверить ещё раз" in section
    assert "🔍 Технические детали" in section
    assert "ads_autopilot_preflight_retry:" in section
    assert "ads_autopilot_preflight_details:" in section
    unknown_controls = section[section.index("elif not provider_known:"):section.index("else:", section.index("elif not provider_known:"))]
    assert "🔧 Исправить настройку" not in unknown_controls


def test_suspended_provider_state_can_confirm_but_unknown_cannot():
    source = (Path(__file__).resolve().parents[1] / "app/bot/menu.py").read_text()
    section = source[source.index('if action_name == "ads_autopilot_enable_confirm":'):source.index('if action_name == "ads_autopilot_enable":', source.index('if action_name == "ads_autopilot_enable_confirm":'))]
    assert 'provider_known = provider_state in {"ON", "SUSPENDED"}' in section
    assert 'eligible = provider_known and not missing' in section
    assert 'elif not provider_known:' in section


def test_final_apply_rechecks_owner_scope_active_link_prerequisites_and_provider():
    source = (Path(__file__).resolve().parents[1] / "app/bot/runtime.py").read_text()
    section = source[source.index('elif action.startswith("ads_autopilot_enable_confirm_apply:"):'):source.index('elif action.startswith("ads_autopilot_disable:"):')]
    for check in (
        "can_access_ads(str(user_id))", "Channel.admin_id == admin.id",
        "profile.owner_admin_id != admin.id", "active_links(db",
        "item.campaign_id == campaign_id", "autopilot_enable_requirements(",
        "_fresh_campaign_provider_state(", 'provider_state not in {"ON", "SUSPENDED"}',
    ):
        assert check in section
    assert section.index("_fresh_campaign_provider_state(") < section.index("set_mode(db")


def test_final_apply_keeps_global_flags_and_provider_execution_separate():
    runtime = (Path(__file__).resolve().parents[1] / "app/bot/runtime.py").read_text()
    section = runtime[runtime.index('elif action.startswith("ads_autopilot_enable_confirm_apply:"):'):runtime.index('elif action.startswith("ads_autopilot_disable:"):')]
    assert "ads_autopilot_enabled =" not in section
    assert "ads_write_actions_enabled =" not in section
    assert "run_check(db" in section and "run_bot_check(db" in section
    assert "provider_execute=" not in section and "yandex_autopilot_adapter" not in section


def test_suspended_campaign_blocks_execution_even_if_recommendation_says_provider_ready():
    assert campaign_state_allows_execution("ON") is True
    assert campaign_state_allows_execution("SUSPENDED") is False
    assert campaign_state_allows_execution("UNKNOWN") is False


def test_set_mode_does_not_create_experiment_or_mutate_global_master_flags():
    source = (Path(__file__).resolve().parents[1] / "app/ads/autopilot.py").read_text()
    section = source[source.index("async def set_mode("):source.index("async def set_budget(")]
    assert "link.execution_mode = AUTONOMOUS if link is selected else ADVISORY" in section
    assert "AdsRecommendation(" not in section and "AdsAiOutcome(" not in section
    assert "ads_autopilot_enabled =" not in section and "ads_write_actions_enabled =" not in section


@pytest.mark.asyncio
async def test_suspended_campaign_can_be_authorized_without_resume_or_experiment_and_isolated():
    import json
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.core.config import settings
    from app.db.models import Admin, AdsAccount, AdsAiOutcome, AdsCampaign, AdsPromotionCampaignLink, AdsRecommendation, Base, Channel

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    old_flags = (settings.ads_autopilot_enabled, settings.ads_write_actions_enabled)
    try:
        async with sessions() as db:
            admin = Admin(external_id="autopilot-owner-test")
            db.add(admin)
            await db.flush()
            channel = Channel(admin_id=admin.id, external_id="channel-main", title="Main", public_slug="autopilot-main")
            other_channel = Channel(admin_id=admin.id, external_id="channel-other", title="Other", public_slug="autopilot-other")
            db.add_all([channel, other_channel])
            account = AdsAccount(admin_id=admin.id, external_account_id="direct-account")
            db.add(account)
            await db.flush()
            first = AdsCampaign(account_id=account.id, external_id="campaign-suspended", name="Paused", state="SUSPENDED", status="SUSPENDED")
            second = AdsCampaign(account_id=account.id, external_id="campaign-same-object", name="Other same object", state="ON", status="ON")
            unrelated = AdsCampaign(account_id=account.id, external_id="campaign-unrelated", name="Unrelated", state="ON", status="ON")
            db.add_all([first, second, unrelated])
            await db.flush()
            links = [
                AdsPromotionCampaignLink(object_type="CHANNEL", channel_id=channel.id, campaign_id=first.id,
                    status="ACTIVE", objective_type="max_subscriber", target_cpa=20, experiment_budget_cap=100,
                    autopilot_config_json=json.dumps({"monthly_budget_limit": 1000, "max_test_spend": 1000}), execution_mode="ADVISORY"),
                AdsPromotionCampaignLink(object_type="CHANNEL", channel_id=channel.id, campaign_id=second.id,
                    status="ACTIVE", objective_type="max_subscriber", target_cpa=20, experiment_budget_cap=100,
                    autopilot_config_json=json.dumps({"monthly_budget_limit": 1000, "max_test_spend": 1000}), execution_mode="AUTONOMOUS"),
                AdsPromotionCampaignLink(object_type="CHANNEL", channel_id=other_channel.id, campaign_id=unrelated.id,
                    status="ACTIVE", objective_type="max_subscriber", target_cpa=20, experiment_budget_cap=100,
                    autopilot_config_json=json.dumps({"monthly_budget_limit": 1000, "max_test_spend": 1000}), execution_mode="AUTONOMOUS"),
            ]
            db.add_all(links)
            await db.flush()
            result = await set_mode(db, object_type="CHANNEL", object_id=channel.id,
                                    mode=AUTONOMOUS, campaign_id=first.id)
            await db.commit()
            assert result["authorized_campaign_ids"] == [first.id]
            assert result["execution_readiness"] == "BLOCKED_BY_CAMPAIGN_STATE"
            assert first.state == "SUSPENDED"
            refreshed = list((await db.scalars(select(AdsPromotionCampaignLink).order_by(AdsPromotionCampaignLink.id))).all())
            modes = {item.campaign_id: item.execution_mode for item in refreshed}
            assert modes[first.id] == AUTONOMOUS
            assert modes[second.id] == ADVISORY
            assert modes[unrelated.id] == AUTONOMOUS
            assert await db.scalar(select(AdsRecommendation.id).limit(1)) is None
            assert await db.scalar(select(AdsAiOutcome.id).limit(1)) is None
            assert (settings.ads_autopilot_enabled, settings.ads_write_actions_enabled) == old_flags
    finally:
        await engine.dispose()


def test_enablement_provider_state_accepts_suspended_but_rejects_unknown():
    assert 'provider_state not in {"ON", "SUSPENDED"}' in (Path(__file__).resolve().parents[1] / "app/bot/runtime.py").read_text()


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
