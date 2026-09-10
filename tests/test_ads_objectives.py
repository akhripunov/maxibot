from decimal import Decimal
from pathlib import Path

import pytest

from app.ads.objectives import evaluate_objective, primary_outcome, validate_currency, validate_target_cpa


def objective(target=20):
    return {
        "objective_mode": "TARGET_CPA",
        "objective_type": "TARGET_CPA",
        "target_cpa": target,
        "currency": "RUB",
        "objective_status": "ACTIVE",
        "primary_outcome": "max_subscriber",
    }


def test_primary_outcome_is_derived_by_object_domain():
    assert primary_outcome("CHANNEL") == "max_subscriber"
    assert primary_outcome("BOT") == "bot_started"


def test_target_validation_rejects_zero_negative_nan_and_bad_currency():
    assert validate_target_cpa("20") == Decimal("20")
    for value in (0, -1, "NaN"):
        with pytest.raises(ValueError):
            validate_target_cpa(value)
    with pytest.raises(ValueError):
        validate_currency("руб")


def test_zero_outcome_never_becomes_zero_cpa():
    result = evaluate_objective(objective(), cost=100, confirmed_outcomes=0)
    assert result["objective_state"] == "INSUFFICIENT_DATA"
    assert result["reason"] == "NO_CONFIRMED_OUTCOMES"
    assert result["actual_cpa"] is None


def test_low_attribution_is_insufficient():
    result = evaluate_objective(objective(), cost=100, confirmed_outcomes=5, attribution_coverage=0.5)
    assert result["objective_state"] == "INSUFFICIENT_DATA"
    assert result["reason"] == "INSUFFICIENT_ATTRIBUTION"


def test_on_target_and_off_target_gap():
    on_target = evaluate_objective(objective(20), cost=100, confirmed_outcomes=5)
    off_target = evaluate_objective(objective(20), cost=125, confirmed_outcomes=5)
    assert on_target["objective_state"] == "ON_TARGET"
    assert on_target["absolute_gap"] == 0
    assert off_target["objective_state"] == "OFF_TARGET"
    assert off_target["absolute_gap"] == 5
    assert off_target["relative_gap"] == 0.25


def test_objective_storage_is_one_existing_link_and_snapshot_json_only():
    model = Path("app/db/models.py").read_text()
    migration = Path("alembic/versions/0079_ads_objective_target_cpa_v1.py").read_text()
    assert "class AdsPromotionCampaignLink" in model
    for field in ("objective_type", "target_cpa", "currency", "objective_status", "objective_updated_at"):
        assert field in model and field in migration
    assert "ads_objectives" not in migration


def test_ui_exposes_goal_without_provider_or_object_internals():
    menu = Path("app/bot/menu.py").read_text()
    assert '"🎯 Цель"' in menu
    assert "objective_for_link" in menu
    assert '"🎯 Цель продвижения"' in menu
    assert '"Цель помогает AI сравнивать фактическую стоимость"' in menu
    assert '"🎯 Установить цель"' in menu
    assert '"🚫 Отключить цель"' not in menu
