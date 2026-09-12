from app.ads.platform import BusinessOutcome, PromotionObjectRef, normalize_max_outcome, outcome_type_for
from app.db.models import (
    AdsAccount, AdsAnalysisSnapshot, AdsAiRun, AdsDataHealth, AdsMemory,
    AdsPromotionCampaignLink,
)


def test_provider_and_destination_platform_are_separate():
    ref = PromotionObjectRef("TELEGRAM", "CHANNEL", "telegram-channel-1")
    assert ref.platform == "TELEGRAM"
    assert ref.object_type == "CHANNEL"
    assert hasattr(AdsAccount, "provider")
    assert not hasattr(AdsPromotionCampaignLink, "provider")


def test_normalized_max_outcomes_keep_existing_business_truth():
    outcome = normalize_max_outcome(object_type="CHANNEL", object_id="max-channel-1")
    assert isinstance(outcome, BusinessOutcome)
    assert outcome.platform == "MAX"
    assert outcome.outcome_type == "max_subscriber"

    bot = normalize_max_outcome(object_type="BOT", object_id="max-bot-1")
    assert bot.outcome_type == "bot_started"


def test_future_telegram_outcomes_are_core_compatible_without_runtime_adapter():
    assert outcome_type_for("TELEGRAM", "CHANNEL") == "telegram_subscriber"
    assert outcome_type_for("TELEGRAM", "BOT") == "telegram_bot_started"
    assert BusinessOutcome("TELEGRAM", "CHANNEL", "tg-1", "telegram_subscriber").platform == "TELEGRAM"


def test_scoped_ads_models_have_destination_platform_identity():
    for model in (AdsPromotionCampaignLink, AdsAnalysisSnapshot, AdsAiRun, AdsMemory, AdsDataHealth):
        assert hasattr(model, "destination_platform")
