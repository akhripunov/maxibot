from app.bot.menu import promotion_yandex_summary


def test_yandex_summary_for_unconnected_channel_is_plain_and_safe():
    text = promotion_yandex_summary("Москва 365", False, None, 0)
    assert text == (
        "⚡ Яндекс\n\n📣 Москва 365\n\n"
        "Статус: не подключено\n"
        "Метрика: —\n"
        "Конверсии: 0"
    )
    for forbidden in ("YANDEX_OAUTH", "SECRET", "callback", "dry-run"):
        assert forbidden not in text


def test_yandex_summary_for_connected_channel_shows_counter_and_conversions():
    assert promotion_yandex_summary("Твой Петербург", True, "123456", 7) == (
        "⚡ Яндекс\n\n📣 Твой Петербург\n\n"
        "Статус: подключено\n"
        "Метрика: 123456\n"
        "Конверсии: 7"
    )
