from pathlib import Path

from app.ads.events import parse_ai_run_event
from app.bot.menu import format_ads_result
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
    assert "self.client.edit_message(run.ui_admin_external_id, run.ui_message_id" in runtime
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
    assert "CPA yandex.ru: ухудшается" in body
    assert body.count("• ") <= 3 + 8
    assert "DEGRADING" not in body
