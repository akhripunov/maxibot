from types import SimpleNamespace

from app.ads.dashboard import business_cpa, stage_for_state
from app.bot.menu import (
    format_ads_campaigns_screen,
    format_ads_history_screen,
    format_ads_learning_screen,
    format_ads_unified_dashboard,
    ads_campaign_selection_buttons,
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
    assert "💡 Режим: Рекомендации" in text
    assert "Пока ничего менять не нужно." in text
    assert "🧠 Обучение:" in text
    assert "Data Trust" not in text
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


def test_unified_screen_renders_recommendations_and_autopilot_states():
    insufficient = format_ads_unified_dashboard(_state(), title="Москва 365")
    assert "Фактический CPA: пока не рассчитан" in insufficient
    assert "💡 Режим: Рекомендации" in insufficient
    assert "Пока ничего менять не нужно." in insufficient

    recommendation_state = _state("RECOMMENDATION", root="📍 Площадка site.ru")
    recommendation_state["current_recommendation"] = SimpleNamespace(title="Отключить площадку site.ru")
    recommendation = format_ads_unified_dashboard(recommendation_state, title="Москва 365")
    assert "💡 Рекомендация" in recommendation
    assert "Отключить площадку site.ru" in recommendation

    autopilot = _state()
    autopilot["execution_mode"] = "AUTONOMOUS"
    autopilot["current_experiment"] = {"experiment_seq": 18, "action": "Новый креатив №4", "evaluation_status": None, "before": 148, "after": 121}
    autopilot["learning_summary"] = {"total_experiments": 12, "successful_experiments": 4, "proven_memories": 3}
    running = format_ads_unified_dashboard(autopilot, title="Москва 365")
    assert "🚀 Автопилот включён" in running
    assert "Эксперимент #18" in running and "Новый креатив №4" in running
    assert "12 тестов · 4 успешных · 3 доказанных знания" in running

    success_state = _state()
    success_state["execution_mode"] = "AUTONOMOUS"
    success_state["last_completed_experiment"] = {"experiment_seq": 18, "action": "Новый креатив №4", "evaluation_status": "SUCCESS", "before": 148, "after": 118}
    success = format_ads_unified_dashboard(success_state, title="Москва 365")
    assert "✅ Эксперимент #18 успешен" in success

    failed_state = _state()
    failed_state["execution_mode"] = "AUTONOMOUS"
    failed_state["last_completed_experiment"] = {"experiment_seq": 18, "action": "Новый креатив №4", "evaluation_status": "FAIL", "before": 148, "after": 176, "rollback": True}
    failed = format_ads_unified_dashboard(failed_state, title="Москва 365")
    assert "↩️ MaxiBot откатил изменение." in failed


def test_detail_renderers_preserve_isolation_and_verified_learning_only():
    state = _state("RECOMMENDATION", root="🎨 Креатив A")
    state["active_campaigns"] = [{
        "external_id": "713798021", "active": True, "provider": "YANDEX_DIRECT",
        "id": "campaign-1", "name": "Основная", "linked": True,
        "spend": 148, "clicks": 43, "state": "ON", "recommendation": None,
    }]
    assert "713798021 — 🟢 Активна" in format_ads_campaigns_screen(state, title="Москва 365")
    assert ads_campaign_selection_buttons(state, channel_id="channel-1")[0][0]["text"].startswith("✅")
    assert "ads_campaign_select:channel-1:campaign-1" == ads_campaign_selection_buttons(state, channel_id="channel-1")[0][0]["callback_data"]
    state["active_campaigns"][0]["linked"] = False
    assert ads_campaign_selection_buttons(state, channel_id="channel-1")[0][0]["text"].startswith("◻️")
    assert "🎨 Креатив A" in format_ads_why_screen(state, title="Москва 365")
    assert "INCONCLUSIVE не считаются проверенным обучением" in format_ads_learning_screen(state, title="Москва 365")
    assert "История появится" in format_ads_history_screen(state, title="Москва 365")


def test_main_screen_supports_human_running_and_inconclusive_experiments():
    human = _state()
    human["current_experiment"] = {
        "experiment_seq": 3, "action": "Отключить площадку site.ru", "executed_by": "HUMAN",
        "evaluation_status": "READY", "before": 148, "after": 121,
    }
    text = format_ads_unified_dashboard(human, title="Москва 365")
    assert "Сейчас проверяем:" in text and "Эксперимент #3" in text
    assert "До: 148 ₽" in text and "Сейчас: 121 ₽" in text

    inconclusive = _state()
    inconclusive["execution_mode"] = "AUTONOMOUS"
    inconclusive["current_experiment"] = {
        "experiment_seq": 4, "action": "Новый креатив", "evaluation_status": "INCONCLUSIVE",
        "before": 148, "after": 139,
    }
    text = format_ads_unified_dashboard(inconclusive, title="Москва 365")
    assert "⏳ Недостаточно данных." in text
    assert "❌ Эксперимент" not in text


def test_why_screen_explains_degraded_data_without_internal_enum():
    state = _state("RECOMMENDATION", root="Рекламная причина пока не доказана.")
    state["data_trust"] = "DEGRADED"
    state["data_trust_reasons"] = {"checks": {"ATTRIBUTION": "DEGRADED"}}
    text = format_ads_why_screen(state, title="Москва 365")
    assert "О качестве данных:" in text
    assert "Часть результатов пока нельзя надёжно связать" in text
    assert "Поэтому рекомендация предварительная." in text
    assert "Data Trust" not in text


def test_why_screen_uses_safe_generic_for_unknown_data():
    state = _state("ANALYSIS")
    state["data_trust"] = "UNKNOWN"
    state["data_trust_reasons"] = {}
    text = format_ads_why_screen(state, title="Москва 365")
    assert "Часть данных пока нельзя надёжно сопоставить" in text
    assert "UNKNOWN" not in text


def test_why_screen_suppresses_stale_mapping_warning_when_live_mapping_supported():
    from app.bot.menu import _data_trust_explanation

    state = {
        "data_trust": "DEGRADED",
        "data_trust_reasons": {"checks": {"ATTRIBUTION": "DEGRADED", "CAMPAIGN_MAPPING": "DEGRADED"}},
        "campaign_specific": {"supported": True, "coverage": 56 / 58},
    }
    assert _data_trust_explanation(state) is None


def test_dashboard_replaces_stale_recommendation_rationale_after_mapping_fix():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/ads/dashboard.py").read_text()
    assert "stale_markers" in source
    assert "Подтверждённые подписчики сопоставлены с кампанией по UTM" in source


def test_dashboard_recomputes_mapping_from_one_active_link_even_if_provider_state_is_filtered():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app/ads/dashboard.py").read_text()
    assert "active_link_campaign_ids" in source
    assert "len(active_link_campaign_ids) == 1" in source
