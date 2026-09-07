from app.bot.menu import promotion_channel_summary


def test_promotion_summary_uses_dashes_for_missing_channel_values():
    assert promotion_channel_summary("Москва 365", None, None, None) == (
        "📣 Продвижение\n\nКанал: Москва 365\n"
        "Ежедневные отчёты: ✅ Включены\n\n"
        "Яндекс: -\nМетрика: -\nЛендинг: -"
    )


def test_promotion_summary_renders_selected_channel_values():
    assert promotion_channel_summary(
        "Твой Петербург",
        "getuniq-u44683-2",
        "12345678",
        "https://go.maxibot.pro/peterburgLanding",
        False,
    ) == (
        "📣 Продвижение\n\nКанал: Твой Петербург\n"
        "Ежедневные отчёты: 🔴 Отключены\n\n"
        "Яндекс: getuniq-u44683-2\n"
        "Метрика: 12345678\n"
        "Лендинг: https://go.maxibot.pro/peterburgLanding"
    )


def test_promotion_summary_hides_tariff_even_when_subscription_is_active():
    from datetime import datetime, timezone

    rendered = promotion_channel_summary(
        "Канал", "Яндекс", "123", "https://example.invalid", subscription_expires_at=datetime(2026, 10, 5, tzinfo=timezone.utc)
    )
    assert "Тариф" not in rendered
    assert "05.10.2026" not in rendered
    assert "Ежедневные отчёты" in rendered
