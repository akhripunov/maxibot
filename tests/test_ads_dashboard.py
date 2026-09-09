from types import SimpleNamespace

from app.ads.dashboard import business_cpa, stage_for_state
from app.bot.menu import (
    format_ads_campaigns_screen,
    format_ads_history_screen,
    format_ads_learning_screen,
    format_ads_unified_dashboard,
    format_ads_why_screen,
)


def test_business_cpa_fails_closed_for_unknown_or_zero_outcomes():
    assert business_cpa(spend=148, confirmed_outcomes=43) == 148 / 43
    assert business_cpa(spend=148, confirmed_outcomes=0) is None
    assert business_cpa(spend=None, confirmed_outcomes=43) is None


def test_stage_mapping_uses_closed_loop_state():
    rec = SimpleNamespace(status="OPEN", data_sufficiency="SUFFICIENT")
    outcome = SimpleNamespace(implementation_confirmed="UNKNOWN", evaluation_status=None)
    assert stage_for_state(recommendation=rec, outcome=outcome, health="HEALTHY", has_data=True, learning_proven=False) == "RECOMMENDATION"
    outcome.implementation_confirmed = "YES"
    assert stage_for_state(recommendation=rec, outcome=outcome, health="HEALTHY", has_data=True, learning_proven=False) == "CHECK"
    outcome.evaluation_status = "SUCCESS"
    assert stage_for_state(recommendation=rec, outcome=outcome, health="HEALTHY", has_data=True, learning_proven=False) == "RESULT"
    assert stage_for_state(recommendation=rec, outcome=outcome, health="HEALTHY", has_data=True, learning_proven=True) == "SCALE"


def test_unified_screen_has_single_action_and_read_only_navigation():
    from app.bot.menu import ads_unified_buttons, format_ads_unified_dashboard

    state = {
        "object": {"type": "BOT", "id": "bot-1"},
        "objective": {"type": "bot_started"}, "target_cpa": None,
        "actual_business_cpa": None, "confirmed_business_outcomes": 0,
        "creative_summary": {"active": 0, "attention": 0},
        "current_stage": "ANALYSIS", "primary_root_cause": None,
        "why_summary": None, "next_best_action": "Продолжать сбор данных.",
        "data_trust": "UNKNOWN", "last_updated_at": None,
    }
    text = format_ads_unified_dashboard(state, title="Test bot")
    assert "Фактический CPA: пока не рассчитан" in text
    assert "Главная проблема:" in text and "Пока недостаточно данных" in text
    labels = [button["text"] for row in ads_unified_buttons("ads_bot", "system", "back") for button in row]
    assert "↻ Обновить сейчас" in labels and "🎨 Креативы" in labels
    assert "Проверить рекламу" not in labels and "Рекомендации" not in labels


def _state(stage="ANALYSIS", *, root=None, outcome=None, proven=False):
    return {
        "object": {"type": "CHANNEL", "id": "channel-1"},
        "objective": {"type": "max_subscriber"}, "target_cpa": 100,
        "currency": "RUB", "actual_business_cpa": None,
        "confirmed_business_outcomes": 0, "creative_summary": {"active": 4, "attention": 0},
        "current_stage": stage, "analysis_running": False, "primary_root_cause": root,
        "root_cause_status": "PROVEN" if root else "NOT_PROVEN", "why_summary": "Данные подтверждают сигнал.",
        "next_best_action": "Продолжать сбор данных.", "data_trust": "HEALTHY",
        "last_updated_at": None, "current_recommendation": None, "current_outcome": outcome,
        "active_campaigns": [], "learning_state": {"status": "PROVEN" if proven else "EXPLORE", "verified_count": 0},
        "memories": [], "memory_evidence_counts": {}, "history": [], "creatives": [],
    }


def test_unified_screen_renders_required_insufficient_recommendation_check_result_and_scale_states():
    insufficient = format_ads_unified_dashboard(_state(), title="Москва 365")
    assert "Фактический CPA: пока не рассчитан" in insufficient
    assert "● Анализ" in insufficient and "○ Масштабирование" in insufficient
    assert "Пока недостаточно данных." in insufficient

    recommendation = format_ads_unified_dashboard(
        _state("RECOMMENDATION", root="📍 Площадка site.ru"), title="Москва 365"
    )
    assert "✓ Анализ" in recommendation and "● Рекомендация" in recommendation
    assert "📍 Площадка site.ru" in recommendation

    checking_state = _state("CHECK", outcome=SimpleNamespace(evaluation_status="READY"))
    checking_state["next_best_action"] = "Ничего не менять до завершения проверки."
    checking = format_ads_unified_dashboard(checking_state, title="Москва 365")
    assert "✓ Рекомендация" in checking and "● Проверка" in checking
    assert "Ничего не менять до завершения проверки." in checking

    success = format_ads_unified_dashboard(
        _state("RESULT", outcome=SimpleNamespace(evaluation_status="SUCCESS")), title="Москва 365"
    )
    assert "✅ Изменение помогло" in success

    failed = format_ads_unified_dashboard(
        _state("RESULT", outcome=SimpleNamespace(evaluation_status="FAIL")), title="Москва 365"
    )
    assert "❌ Изменение не помогло" in failed

    scale = format_ads_unified_dashboard(_state("SCALE", proven=True), title="Москва 365")
    assert "● Масштабирование" in scale and "🚀 Подход подтверждён." in scale


def test_detail_renderers_preserve_isolation_and_verified_learning_only():
    state = _state("RECOMMENDATION", root="🎨 Креатив A")
    state["active_campaigns"] = [{
        "external_id": "713798021", "active": True, "provider": "YANDEX_DIRECT",
        "spend": 148, "clicks": 43, "state": "ON", "recommendation": None,
    }]
    assert "713798021 — 🟢 Активна" in format_ads_campaigns_screen(state, title="Москва 365")
    assert "🎨 Креатив A" in format_ads_why_screen(state, title="Москва 365")
    assert "INCONCLUSIVE не считаются проверенным обучением" in format_ads_learning_screen(state, title="Москва 365")
    assert "История появится" in format_ads_history_screen(state, title="Москва 365")
