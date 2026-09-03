from app.core.config import settings


def default_welcome() -> str:
    # Keep the standalone custom-bot fallback unchanged. Channel defaults use
    # default_channel_welcome() below and are normalized separately.
    return "✅ Напишите ваш вопрос —\nя передам администратору."


def default_channel_welcome() -> str:
    return "📩 Есть новость или интересный материал?\nПришлите сюда текст, фото или видео — редакция канала всё посмотрит."


def default_sent() -> str:
    return "✅ Спасибо! Материал получен и передан редакции на рассмотрение."


def editable_welcome(text: str | None) -> str:
    """Return only the administrator-editable welcome text.

    Older rows may still contain the former branding footer.  Strip it at
    render/save boundaries so branding is never treated as user content.
    """
    value = (text or "").strip()
    lines = [
        line for line in value.splitlines()
        if not line.strip().startswith("Бот создан с помощью")
    ]
    return "\n".join(lines).strip()


_OLD_CHANNEL_WELCOME_DEFAULTS = {
    "✅ Напишите ваш вопрос —\nя передам администратору.",
    "✅ Напишите ваш вопрос — я передам администратору.",
    "✅ Напишите ваш вопрос — я передам его администратору.",
}
_OLD_CHANNEL_SENT_DEFAULTS = {
    "✅ Отправлено администратору.\nОжидайте ответ.",
}


def normalize_channel_welcome(text: str | None) -> str:
    value = editable_welcome(text)
    return default_channel_welcome() if not value or value in _OLD_CHANNEL_WELCOME_DEFAULTS else value


def normalize_channel_sent(text: str | None) -> str:
    value = (text or "").strip()
    return default_sent() if not value or value in _OLD_CHANNEL_SENT_DEFAULTS else value


def render_channel_welcome_message(text: str | None) -> str:
    """Render a channel welcome with the immutable MAXIBOT footer."""
    body = normalize_channel_welcome(text)
    username = settings.max_bot_username.lstrip("@").strip()
    footer = f"Бот создан с помощью [MAXIBOT](https://max.ru/{username})"
    return f"{body}\n\n{footer}"


def promotion_landing_description(text: str | None) -> str:
    """Keep Suggestion/custom-bot copy out of traffic landing pages."""
    value = (text or "").strip()
    if "Бот создан с помощью" in value or "[Предложка]" in value:
        return "Переходим в MAX…"
    return value or "Переходим в MAX…"


def menu_text(channels, bots_or_accepting, bots=None):
    # Keep the historical three-argument contract used by the demo/tests while
    # allowing older callers to pass the former accepting counter.
    if bots is None:
        bots = bots_or_accepting
    # ``bots_or_accepting`` is retained for call compatibility only.
    return (
    "🤖 <b>MAXIBOT</b>\n\n"
    "Умное продвижение каналов MAX через Яндекс.Директ.\n\n"
    "MAXIBOT создаёт персональную ссылку на лендинг канала, отслеживает переходы и подписки и автоматически передаёт подтверждённые конверсии обратно в Яндекс.Директ.\n\n"
    "Благодаря этому Яндекс обучается на реальных подписках, находит более подходящую аудиторию, а стоимость подписчика со временем снижается."
)
def channel_card_text(title, accepting, users, messages, connected_at, public_url):
    icon="🟢" if accepting else "⛔"; status="принимает сообщения" if accepting else "не принимает сообщения"
    return f"📕 {title}\n\n{icon} Статус: {status}\n👥 Пользователей: {users}\n💬 Всего сообщений: {messages}\n📅 Подключён: {connected_at}\n\n🔗 Персональная ссылка:\n{public_url}"
WELCOME=default_channel_welcome()
SENT=default_sent()
NO_CHANNELS="📩 Обращения / Предложка\n\nУ вас пока нет подключённых каналов."
SETTINGS="⚙️ Настройка сообщений пользователя\n\nВыберите, какой текст нужно изменить."
