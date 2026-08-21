from datetime import datetime, timezone

from app.daily_reports import chat_subscriber_count, render_daily_report, report_date_due


def test_daily_report_is_due_at_0830_moscow_for_the_prior_day():
    assert report_date_due(datetime(2026, 8, 20, 5, 29, tzinfo=timezone.utc)) is None
    assert str(report_date_due(datetime(2026, 8, 20, 5, 30, tzinfo=timezone.utc))) == "2026-08-19"


def test_daily_report_reads_only_an_explicit_member_count():
    assert chat_subscriber_count({"chat": {"participants_count": 1837}}) == 1837
    assert chat_subscriber_count({"result": {"members_count": 9}}) == 9
    assert chat_subscriber_count({"title": "no count"}) is None


def test_daily_report_keeps_unknown_unsubscribe_source_fail_closed():
    text = render_daily_report("Твой Петербург", 1837, -1, 0, 1, 0)
    assert text == (
        "⚡️ Дэйли: Твой Петербург\n"
        "Подписчиков: 1837 (-1)\n\n"
        "✅ Подписалось: +0\n\n"
        "⛔️ Отписалось: -1\n"
        "Пригласительные ссылки: -0\n"
        "Не определено: -1"
    )
