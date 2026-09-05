from pathlib import Path

from app.ads.events import parse_ai_run_event
from app.bot.menu import format_ads_result, safe_max_entity_label, format_dimension_coverage
from datetime import datetime, timezone


def test_terminal_ai_event_parser_accepts_only_terminal_states():
    assert parse_ai_run_event('{"run_id":"run-1","status":"CODEX_COMPLETED"}') == (
        "run-1",
        "CODEX_COMPLETED",
    )
    assert parse_ai_run_event('{"run_id":"run-1","status":"WAITING_FOR_CODEX"}') is None
    assert parse_ai_run_event("not-json") is None


def test_ai_result_flow_publishes_after_commit_and_updates_saved_origin():
    main = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    runtime = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    service = (Path(__file__).parents[1] / "app" / "ads" / "service.py").read_text()
    assert "await persist_development_result" in main
    assert "await publish_ai_run_event(run.id, run.status)" in main
    assert "ui_message_id: str | None = None" in service
    assert "run.ui_message_id" in runtime
    assert "run.ui_admin_external_id,\n                run.ui_message_id," in runtime
    assert "disable_link_preview=True" in runtime
    assert "self.client.send_message(run.ui_admin_external_id" not in runtime


def test_unified_ads_result_renderer_formats_numbers_time_confidence_and_shadow_mode():
    body = format_ads_result(
        headline="🟢 Анализ завершён",
        channel_title="Москва 365",
        direct_connected=True,
        campaign_name="msk365_rsya_msk_click_test1",
        ai_status="✅ Завершён",
        checked_at=datetime(2026, 9, 3, 6, 40, tzinfo=timezone.utc),
        fact_values={"cost_7d": 881.59, "conversions_7d": 43.0, "actual_cpa_7d": 20.502093023255814, "maxibot_qualified_membership_rate": 0.0211},
        diagnosis="Атрибуция требует проверки.",
        recommendation="Сначала проверить атрибуцию.",
        confidence="HIGH",
        completed=True,
    )
    assert "881,59 ₽" in body
    assert "Конверсии: 43" in body
    assert "20,50 ₽" in body
    assert "Конверсия визит → подписка: 2,11%" in body
    assert "покрытие" not in body.lower()
    assert "03.09 09:40" in body
    assert "Уверенность: высокая" in body
    assert "🔒 Теневой режим" in body
    assert "Shadow mode" not in body


def test_result_renderer_shows_only_bounded_russian_trend_block():
    body = format_ads_result(
        headline="done", channel_title="Москва 365", direct_connected=True,
        campaign_name="test", ai_status="done", checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={"cost_7d": 10, "conversions_7d": 2, "actual_cpa_7d": 5}, diagnosis="ok",
        recommendation="наблюдать", confidence="HIGH", completed=True,
        trends={"findings": [
            {"metric": "cpa", "entity": "yandex.ru", "trend_state": "DEGRADING", "relative_delta": .25},
            {"metric": "cpa", "entity": "other.ru", "trend_state": "STABLE", "relative_delta": .01},
            {"metric": "cvr", "entity": "Android", "trend_state": "IMPROVING", "relative_delta": .20},
            {"metric": "cpc", "entity": "extra.ru", "trend_state": "VOLATILE", "relative_delta": .30},
        ]},
    )
    assert "📈 Динамика" in body
    assert "CPA yandex[.]ru: ухудшается" in body
    assert body.count("• ") <= 3 + 8
    assert "DEGRADING" not in body
    assert "713798021" not in body


def test_granular_report_uses_grounded_placement_and_single_ad_state():
    body = format_ads_result(
        headline="done", channel_title="Москва 365", direct_connected=True,
        campaign_name="campaign", ai_status="done", checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={"cost_7d": 100, "conversions_7d": 5, "actual_cpa_7d": 20},
        diagnosis="При высокой уверенность в данных Яндекса.",
        recommendation="Выполнить read-only проверку.", confidence="HIGH", completed=True,
        v2_analysis={"report_view": {
            "primary_findings": [{"dimension": "PLACEMENT", "label": "yandex.ru", "finding_type": "HIGH_CPA",
                                  "metrics": {"cpa": 35.06, "cost_share": .308}, "campaign_cpa": 20}],
            "comparison": {"ad": {"available": 1, "included": 1, "state": "SINGLE_ENTITY"},
                            "placement": {"available": 3, "included": 3, "state": "AVAILABLE"}},
        }},
    )
    assert "Площадка yandex[.]ru" in body
    assert "сравнивать креативы пока не с чем" in body
    assert "при высокой уверенность" not in body.lower()
    assert "read-only" not in body.lower()


def test_granular_report_does_not_claim_unavailable_dimensions():
    body = format_ads_result(
        headline="done", channel_title="Москва 365", direct_connected=True,
        campaign_name="campaign", ai_status="done", checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={}, diagnosis="ok", recommendation="наблюдать", confidence="HIGH", completed=True,
        v2_analysis={"report_view": {"primary_findings": [], "comparison": {
            "ad": {"available": 1, "included": 1, "state": "SINGLE_ENTITY"},
            "device": {"available": 0, "included": 0, "state": "PROVIDER_UNAVAILABLE"},
        }}},
    )
    assert "Объявления: доступно 1" in body
    assert "устройства" not in body


def test_granular_report_polish_humanizes_entities_and_uses_explicit_coverage():
    v2 = {"report_view": {
        "primary_findings": [{"dimension": "PLACEMENT", "label": "yandex.ru", "finding_type": "HIGH_CPA",
                              "metrics": {"cpa": 36.52, "cost_share": .3262}, "campaign_cpa": 19.91,
                              "materiality": "HIGH", "sample_sufficiency": "SUFFICIENT"}],
        "comparison": {
            "ad": {"available": 1, "included": 1, "material_findings": 0, "state": "SINGLE_ENTITY"},
            "placement": {"available": 381, "included": 25, "material_findings": 1, "state": "AVAILABLE"},
            "region": {"available": 193, "included": 0, "material_findings": 0, "state": "NO_MATERIAL_DIFFERENCE"},
            "demographics": {"available": 17, "included": 0, "material_findings": 0, "state": "NO_MATERIAL_DIFFERENCE"},
        },
    }}
    body = format_ads_result(
        headline="done", channel_title="Москва 365", direct_connected=True,
        campaign_name="campaign", ai_status="done", checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={}, diagnosis="Основной кандидат — размещение внешний источник с CPA 36,52.",
        recommendation="Проверить размещение внешний источник. attribution_confidence низкая",
        confidence="HIGH", completed=True, v2_analysis=v2,
        trends={"findings": [{"dimension": "DEMOGRAPHICS", "entity": "AGE_45_54|GENDER_FEMALE", "metric": "cpa", "trend_state": "IMPROVING", "relative_delta": -.269}]},
    )
    assert "площадка yandex[.]ru" in body.lower()
    assert "CPA 36,52 ₽" in body
    assert "внешний источник" not in body
    assert "attribution_confidence" not in body
    assert "Женщины 45–54" in body
    assert "🧭 Что проверил ИИ-директолог" in body
    assert "Площадки: доступно 381 · отобрано 25 · 1 значимый вывод" in body
    assert "Регионы: доступно 193 · значимых выводов нет" in body


def test_safe_max_entity_label_breaks_bare_domain_autolinking_without_changing_identity():
    assert safe_max_entity_label("yandex.ru") == "yandex[.]ru"
    assert safe_max_entity_label("ru.yandex.weatherplugin") == "ru[.]yandex[.]weatherplugin"
    assert safe_max_entity_label("Мобильные") == "Мобильные"


def test_live_completion_payload_path_uses_canonical_renderer_contract():
    from pathlib import Path
    runtime = (Path(__file__).parents[1] / "app" / "bot" / "runtime.py").read_text()
    menu = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    section = runtime[runtime.index("async def _handle_ads_ai_event"):runtime.index("    async def handle(")]
    assert "self.repo.render" in section
    assert "format_ads_result" in menu
    assert "build_report_view" in menu


def _quality_render_snapshot(action="PLACEMENT_REVIEW", *, evolution="UNKNOWN", wait=None, outcome="UNKNOWN"):
    return {"QUALITY_DECISION_SNAPSHOT": {"primary_candidate": {
        "safe_label": "yandex[.]ru", "action_level": action, "data_sufficiency": "SUFFICIENT",
        "relative_deviation": .82, "spend_share": .3122, "signal_evolution": evolution,
        "WHY_THIS": "Сигнал основан на сравнении с итогом кампании и отобранных данных.",
        "WHY_NOW": "Есть достаточная выборка для проверки.",
        "NEXT_STEP": "Проверить сигнал вручную; автоматические изменения отключены.",
        "WAIT_CONDITION": wait,
    }, "history_state": "AVAILABLE", "outcome_state": outcome},
        "report_view": {"primary_findings": [{"dimension": "PLACEMENT", "label": "yandex.ru", "finding_type": "HIGH_CPA", "metrics": {"cpa": 36.52, "cost_share": .3122}, "campaign_cpa": 20}], "comparison": {"placement": {"available": 381, "included": 25, "state": "AVAILABLE"}}}}


def _render_quality(snapshot):
    return format_ads_result(
        headline="done", channel_title="Москва 365", direct_connected=True,
        campaign_name="campaign", ai_status="done", checked_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        fact_values={"cost_7d": 100, "conversions_7d": 5, "actual_cpa_7d": 20},
        diagnosis="общая оценка", recommendation="fallback", confidence="HIGH", completed=True,
        v2_analysis=snapshot,
    )


def test_quality_renderer_uses_placement_review_without_conflicting_watch_copy():
    body = _render_quality(_quality_render_snapshot("PLACEMENT_REVIEW"))
    assert "Проверить вручную" in body
    assert "Можно рассмотреть изменение" not in body
    assert "Пока только наблюдать" not in body


def test_quality_renderer_maps_watch_and_no_action():
    assert "Наблюдать" in _render_quality(_quality_render_snapshot("WATCH"))
    assert "Ничего менять не нужно" in _render_quality(_quality_render_snapshot("NO_ACTION"))


def test_quality_renderer_maps_change_levels_and_has_one_sufficiency_sentence():
    body = _render_quality(_quality_render_snapshot("CONSIDER_CHANGE"))
    assert "Можно рассмотреть изменение" in body
    assert body.count("Выборки достаточно для проверки сигнала") == 1
    assert "CONSIDER_CHANGE" not in body
    assert body.count("Автоматические изменения отключены") == 1


def test_quality_renderer_maps_investigate_and_strong_recommendation():
    assert "Проверить вручную" in _render_quality(_quality_render_snapshot("INVESTIGATE"))
    assert "Есть веские основания для изменения" in _render_quality(_quality_render_snapshot("STRONG_RECOMMENDATION"))


def test_quality_renderer_shows_history_only_for_proven_evolution():
    body = _render_quality(_quality_render_snapshot("WATCH", evolution="STRENGTHENING"))
    assert "Сигнал усиливается по сравнению с прошлой проверкой." in body
    assert "История: история доступна" not in body
    unknown = _render_quality(_quality_render_snapshot("WATCH", evolution="UNKNOWN"))
    assert "Динамика сигнала:" not in unknown


def test_quality_renderer_shows_wait_once_and_keeps_pending_pending():
    body = _render_quality(_quality_render_snapshot("INVESTIGATE", wait="ещё нужны данные", outcome="PENDING"))
    assert body.count("Условие ожидания: ещё нужны данные") == 1
    assert "Измерение результата ещё ожидается" in body
    assert "Измеренный результат доступен" not in body


def test_quality_renderer_does_not_repeat_primary_label_in_summary_and_recommendation():
    body = _render_quality(_quality_render_snapshot("PLACEMENT_REVIEW"))
    assert body.count("yandex[.]ru") == 1
    assert "WHY_THIS" not in body
    assert "WHY_NOW" not in body


def test_exact_live_consider_change_copy_contract():
    body = _render_quality(_quality_render_snapshot("CONSIDER_CHANGE", evolution="STRENGTHENING", outcome="PENDING"))
    assert "Можно рассмотреть изменение." in body
    assert "Почему это важно: CPA отклоняется от среднего по кампании на 82,00%" in body
    assert "Почему сейчас: текущие метрики дают проверяемый сигнал." in body
    assert "Выборки достаточно для проверки сигнала." in body
    assert "Сигнал усиливается по сравнению с прошлой проверкой." in body
    assert "Измерение результата ещё ожидается." in body
    assert "CONSIDER_CHANGE" not in body
    assert "SUFFICIENT" not in body
    assert "PENDING" not in body
    assert body.count("Автоматические изменения отключены") == 1
    assert body.count("yandex[.]ru") == 1
