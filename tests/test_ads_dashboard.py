from types import SimpleNamespace

from app.ads.dashboard import business_cpa, stage_for_state


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
