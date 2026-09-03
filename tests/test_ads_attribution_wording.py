from datetime import datetime, timezone

from app.ads.llm import SYSTEM_PROMPT
from app.bot.menu import format_ads_result


def test_membership_rate_has_canonical_user_semantics_and_separate_scope():
    body = format_ads_result(
        headline="done", channel_title="Москва 365", direct_connected=True,
        campaign_name="test", ai_status="done",
        checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={
            "qualified_subscriptions": 50,
            "landing_visits": 1305,
            "maxibot_qualified_membership_rate": 50 / 1305,
            "conversions_7d": 48,
            "cost_7d": 1021.59,
            "actual_cpa_7d": 21.28,
        },
        diagnosis="Проверка", recommendation="Наблюдать", confidence="HIGH",
        completed=True,
    )
    assert "50 подтверждённых подписок из 1 305 визитов" in body
    assert "Конверсия визит → подписка: 3,83%" in body
    assert "Сопоставление с конкретной рекламной кампанией: недоступно" in body
    assert "покрытие атрибуции" not in body.lower()
    assert "подтверждение конверсий" not in body.lower()
    assert "48" in body and "21,28 ₽" in body


def test_ai_prompt_declares_membership_rate_and_separates_yandex():
    assert "MAXIBOT_QUALIFIED_MEMBERSHIP_RATE" in SYSTEM_PROMPT
    assert "qualified_memberships" in SYSTEM_PROMPT
    assert "tracked_landing_visits" in SYSTEM_PROMPT
    assert "visit-to-qualified-membership" in SYSTEM_PROMPT
    assert "Never call it attribution coverage" in SYSTEM_PROMPT
    assert "Keep Yandex conversions and MaxiBot memberships as separate populations" in SYSTEM_PROMPT
