from pathlib import Path

import pytest

from app.ads.autopilot import (
    ADVISORY,
    AUTONOMOUS,
    AUTOPILOT_ACTION_ALLOWLIST,
    default_autopilot_config,
    validate_action,
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
