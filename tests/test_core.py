from app.domain.services import channel_card_text, menu_text

def test_main_menu_contains_counters():
    text = menu_text(2, 1, 7)
    assert text == (
        "🤖 <b>MAXIBOT</b>\n\n"
        "Умное продвижение каналов MAX через Яндекс.Директ.\n\n"
        "MAXIBOT создаёт персональную ссылку на лендинг канала, отслеживает переходы и подписки и автоматически передаёт подтверждённые конверсии обратно в Яндекс.Директ.\n\n"
        "Благодаря этому Яндекс обучается на реальных подписках, находит более подходящую аудиторию, а стоимость подписчика со временем снижается."
    )

def test_channel_card_reflects_disabled_state():
    text = channel_card_text("Петербург 24", False, 1, 3, "2026-08-05 12:00", "abc")
    assert "⛔ Статус: не принимает сообщения" in text
    assert "✅ Включить приём сообщений" not in text
