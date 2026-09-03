from datetime import datetime, timezone

from app.daily_reports import chat_subscriber_count, channel_existed_on_report_day, render_daily_report, report_date_due


def test_daily_report_is_due_at_0830_moscow_for_the_prior_day():
    assert report_date_due(datetime(2026, 8, 20, 5, 29, tzinfo=timezone.utc)) is None
    assert str(report_date_due(datetime(2026, 8, 20, 5, 30, tzinfo=timezone.utc))) == "2026-08-19"


def test_new_channel_is_not_reported_until_the_following_day():
    report_day = datetime(2026, 8, 19, tzinfo=timezone.utc).date()
    connected_today = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    connected_yesterday = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
    assert not channel_existed_on_report_day(connected_today, report_day)
    assert channel_existed_on_report_day(connected_yesterday, report_day)


def test_daily_report_reads_only_an_explicit_member_count():
    assert chat_subscriber_count({"chat": {"participants_count": 1837}}) == 1837
    assert chat_subscriber_count({"result": {"members_count": 9}}) == 9
    assert chat_subscriber_count({"title": "no count"}) is None


def test_daily_report_keeps_unknown_unsubscribe_source_fail_closed():
    text = render_daily_report("Твой Петербург", 1837, -1, 0, 1, 0, 0, 0)
    assert text == (
        "⚡️ Дэйли: Твой Петербург\n"
        "Подписчиков сейчас: 1837\n"
        "Изменение за прошлый день: -1\n\n"
        "✅ Подписалось: +0\n"
        "   ├─ подтверждено из рекламы: +0\n"
        "   └─ источник не подтверждён: +0\n\n"
        "⛔️ Отписалось: -1\n"
        "   ├─ подтверждено из рекламы: -0\n"
        "   └─ источник не подтверждён: -1\n"
        "Пригласительные ссылки: -0\n"
        "Не определено: -1"
    )


def test_daily_report_does_not_call_unattributed_members_organic():
    text = render_daily_report("Москва 365", 38, 1, 3, 0, 0, 1, 2)
    assert "подтверждено из рекламы: +1" in text
    assert "источник не подтверждён: +2" in text
    assert "органически" not in text


def test_daily_report_classifies_confirmed_ad_unsubscribes_separately():
    text = render_daily_report("Москва 365", 41, 2, 4, 2, 0, 0, 4, 1)
    assert "Отписалось: -2" in text
    assert "подтверждено из рекламы: -1" in text
    assert "источник не подтверждён: -1" in text
