from app.bot.menu import promotion_channel_summary


def test_promotion_summary_uses_dashes_for_missing_channel_values():
    assert promotion_channel_summary("Москва 365", None, None, None) == (
        "📣 Продвижение\n\nКанал: Москва 365\n\n"
        "Яндекс: -\nМетрика: -\nЛендинг: -"
    )


def test_promotion_summary_renders_selected_channel_values():
    assert promotion_channel_summary(
        "Твой Петербург",
        "getuniq-u44683-2",
        "12345678",
        "https://go.maxibot.pro/peterburgLanding",
    ) == (
        "📣 Продвижение\n\nКанал: Твой Петербург\n\n"
        "Яндекс: getuniq-u44683-2\n"
        "Метрика: 12345678\n"
        "Лендинг: https://go.maxibot.pro/peterburgLanding"
    )
