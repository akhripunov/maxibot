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


def menu_text(channels, bots_or_accepting, bots=None):
    # Keep the historical three-argument contract used by the demo/tests while
    # allowing the production menu to omit the optional accepting counter.
    if bots is None:
        bots = bots_or_accepting
        accepting = None
    else:
        accepting = bots_or_accepting
    return (
    "🤖 MAXIBOT\n\n"
    "Настройте обратную связь для ваших каналов MAX.\n\n"
    "📋 Каналы — подключайте каналы и получайте обращения подписчиков "
    "в отдельные рабочие чаты.\n\n"
    "🤖 Боты — подключайте собственных ботов, чтобы подписчики писали "
    "и получали ответы от имени вашего бренда.\n\n"
    f"📚 Подключено каналов: {channels}\n"
    + (f"✅ Принимают сообщения: {accepting}\n" if accepting is not None else "")
    + f"🤖 Подключено своих ботов: {bots}"
)
def channel_card_text(title, accepting, users, messages, connected_at, public_url):
    icon="🟢" if accepting else "⛔"; status="принимает сообщения" if accepting else "не принимает сообщения"
    return f"📕 {title}\n\n{icon} Статус: {status}\n👥 Пользователей: {users}\n💬 Всего сообщений: {messages}\n📅 Подключён: {connected_at}\n\n🔗 Персональная ссылка:\n{public_url}"
WELCOME=default_channel_welcome()
SENT=default_sent()
NO_CHANNELS="📋 Мои каналы\n\nУ вас пока нет подключённых каналов.\n\nЧтобы подключить канал:\n\n1. Добавьте MAXIBOT в подписчики канала\n   через название бота.\n\n2. Назначьте его администратором\n   с правом «Редактировать сообщения».\n\n3. Перешлите сюда любой пост из канала.\n\nПосле этого канал появится в списке."
SETTINGS="⚙️ Настройка сообщений пользователя\n\nВыберите, какой текст нужно изменить."
