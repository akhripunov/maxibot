from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import secrets
from zoneinfo import ZoneInfo
from urllib.parse import unquote
from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Admin, Channel, ChannelSlugRegistry, Conversation, Message, BlockedUser, AdminFeedMessage, SupportTicket, SupportTicketMessage, BotConnection, CustomBotSession, CustomBotMessage, LandingPage, TrafficProfile, TrafficVisit, MembershipEvent, TrafficMembership, TrafficEvent, TrafficAttributionCandidate, MaxIdentityLink, TrafficSubscription, OfflineConversion, MaxJoinEvent
from app.traffic.attribution import decide_scored, HIGH, MEDIUM, EXACT, can_send_offline_conversion, resolve_exact_identity_visit
from app.domain.services import menu_text, WELCOME, SENT, editable_welcome, normalize_channel_welcome, normalize_channel_sent, promotion_landing_description
from app.core.config import settings
from app.traffic.deep_link import valid_max_target
from app.integrations.yandex_oauth import authorize_url, configured, connection_status, create_state
from app.integrations.yandex_metrica import channel_setup_status
from app.integrations.max_connection import PRIMARY, BOT_CONNECTION

logger = logging.getLogger(__name__)


def channel_connection_instructions() -> str:
    """Single canonical copy used by every channel-connect entry point."""
    return (
        "📢 Подключение канала\n\n"
        "Чтобы подключить канал, выполните следующие шаги:\n\n"
        "1. Откройте настройки вашего канала\n"
        "2. Перейдите в раздел «Подписчики»\n"
        "3. Нажмите «Добавить подписчиков»\n"
        "4. Найдите в поиске «MAXIBOT» и добавьте"
    )


def promotion_intro_text() -> str:
    """Canonical promotion explanation shown before channel selection."""
    return (
        "📣 Продвижение\n\n"
        "Привлекайте новых подписчиков в каналы MAX\n"
        "через рекламу в Яндекс Директе.\n\n"
        "MAXIBOT отслеживает переходы и подписки,\n"
        "а Яндекс Директ обучается на реальных конверсиях.\n"
        "Со временем это помогает находить более подходящую\n"
        "аудиторию и снижать стоимость подписчика.\n\n"
        "Выберите канал для продвижения:"
    )


def promotion_connect_text() -> str:
    """Short instructions shown when the owner adds a promotion channel."""
    return (
        "📣 Подключение канала для продвижения\n\n"
        "Добавьте MAXIBOT в канал — после этого он\n"
        "автоматически появится в списке продвижения.\n\n"
        "1. Откройте настройки вашего канала\n"
        "2. Перейдите в раздел «Подписчики»\n"
        "3. Нажмите «Добавить подписчиков»\n"
        "4. Найдите в поиске «MAXIBOT» и добавьте"
    )


def home_menu_banner_media() -> list[dict]:
    """Return the single decorative attachment used by the home screen.

    The URL is derived from the configured public traffic host so staging and
    production never cross-load one another's asset.  Other menu screens must
    remain text-only.
    """
    asset_url = f"{settings.traffic_public_base_url.rstrip('/')}/static/images/maxibot_home_banner_compact.webp"
    return [{"type": "image", "payload": {"url": asset_url}}]


def _time(value) -> str:
    if not value:
        return ""
    normalized = value.astimezone(timezone.utc) if value.tzinfo else value
    return normalized.strftime("%H:%M")


def _author_color(user_external_id: str) -> str:
    """Stable visual author color for the lifetime of a channel feed."""
    palette = ("🔵", "🟢", "🟣", "🟠", "🔴", "🟡", "🟤")
    digest = hashlib.sha256(str(user_external_id).encode()).digest()
    return palette[digest[0] % len(palette)]


def _screen_args(screen: str, fallback: str | None = None) -> tuple[str | None, int]:
    bits = screen.split(":")
    selected = bits[1] if len(bits) > 1 and bits[1] else fallback
    try:
        page = int(bits[2]) if len(bits) > 2 else 0
    except ValueError:
        page = 0
    return selected, page


def buttons(*rows: tuple[str, str] | list[tuple[str, str]]) -> list[list[dict]]:
    return [[{"text": text, "callback_data": action} for text, action in row] if isinstance(row, list) else [{"text": row[0], "callback_data": row[1]}] for row in rows]


def personal_link(public_slug: str, bot_username: str | None = None) -> str:
    username = (bot_username or settings.max_bot_username).lstrip("@").strip()
    if not username or not public_slug:
        raise ValueError("bot_username and public_slug are required")
    return f"https://max.ru/{username}?start={public_slug}"


def public_landing_url(public_id: str) -> str:
    """Build the stable public landing URL without duplicate slashes."""
    if not public_id or "/" in public_id or "\\" in public_id:
        raise ValueError("public_id must be a single opaque path segment")
    return f"{settings.traffic_public_base_url.rstrip('/')}/{public_id.lstrip('/')}"


def new_landing_public_id() -> str:
    """Return a short, public, numeric landing identifier.

    This identifier only addresses the landing. Per-visit attribution remains
    protected by the separately generated one-time startapp token.
    """
    return str(secrets.randbelow(90_000_000_000_000) + 10_000_000_000_000)


def promotion_channel_summary(channel_title: str, yandex_value: str | None,
                              counter_id: str | None, landing_url: str | None) -> str:
    """Render only channel-scoped, non-secret promotion readiness fields."""
    return (
        f"📣 Продвижение\n\nКанал: {channel_title}\n\n"
        f"Яндекс: {yandex_value or '-'}\n"
        f"Метрика: {counter_id or '-'}\n"
        f"Лендинг: {landing_url or '-'}"
    )


def promotion_yandex_summary(channel_title: str, connected: bool,
                             counter_id: str | None, conversions: int) -> str:
    return (
        f"⚡ Яндекс\n\n📣 {channel_title}\n\n"
        f"Статус: {'подключено' if connected else 'не подключено'}\n"
        f"Метрика: {counter_id or '—'}\n"
        f"Конверсии: {conversions}"
    )


async def traffic_stats(db: AsyncSession, channel_id: str) -> dict[str, int | float]:
    visits = int(await db.scalar(select(func.count(TrafficVisit.id)).where(TrafficVisit.channel_id == channel_id)) or 0)
    opens = int(await db.scalar(select(func.count(TrafficVisit.id)).where(TrafficVisit.channel_id == channel_id, TrafficVisit.max_opened_at.is_not(None))) or 0)
    to_channel = int(await db.scalar(select(func.count(TrafficVisit.id)).where(TrafficVisit.channel_id == channel_id, TrafficVisit.to_channel_at.is_not(None))) or 0)
    new_members = int(await db.scalar(select(func.count(TrafficMembership.id)).where(TrafficMembership.channel_id == channel_id)) or 0)
    unsubscribed = int(await db.scalar(select(func.count(TrafficMembership.id)).where(TrafficMembership.channel_id == channel_id, TrafficMembership.current_member.is_(False))) or 0)
    statuses = {}
    for status in ("EXACT", "HIGH", "MEDIUM", "AMBIGUOUS", "UNATTRIBUTED"):
        statuses[status] = int(await db.scalar(select(func.count(TrafficMembership.id)).where(TrafficMembership.channel_id == channel_id, TrafficMembership.attribution_status == status)) or 0)
    likely = int(await db.scalar(select(func.count(TrafficEvent.id)).where(TrafficEvent.channel_id == channel_id, TrafficEvent.event_type == "MAX_OPEN_LIKELY")) or 0)
    confirmed = int(await db.scalar(select(func.count(TrafficEvent.id)).where(TrafficEvent.channel_id == channel_id, TrafficEvent.event_type == "MAX_OPEN_CONFIRMED")) or 0)
    joins = int(await db.scalar(select(func.count(TrafficEvent.id)).where(TrafficEvent.channel_id == channel_id, TrafficEvent.event_type.in_(["join_request_created", "join_request"]))) or 0)
    return {"visits": visits, "opens": opens, "to_channel": to_channel, "new_members": new_members, "unsubscribed": unsubscribed,
            "max_open_likely": likely, "max_open_confirmed": confirmed, "join_events": joins, **statuses}


def official_bot_added_diagnostics(chat: dict, initiator_user_id: str | None) -> dict[str, object]:
    """Return safe validation facts for a primary ``bot_added`` response.

    This intentionally contains no raw owner ID or URL.  It is used for
    production diagnostics when the fail-closed contract rejects metadata.
    """
    if not isinstance(chat, dict):
        return {
            "chat_is_dict": False,
            "chat_type": None,
            "owner_present": False,
            "owner_type": None,
            "initiator_present": bool(initiator_user_id),
            "owner_matches_initiator": False,
            "link_present": False,
            "link_type": None,
            "link_valid": False,
        }
    chat_type = chat.get("chat_type") or chat.get("type")
    owner = chat.get("owner_id") or chat.get("owner_user_id")
    if isinstance(owner, dict):
        owner = owner.get("user_id") or owner.get("id")
    if owner is None and isinstance(chat.get("owner"), dict):
        owner = chat["owner"].get("user_id") or chat["owner"].get("id")
    if owner is None and isinstance(chat.get("owner"), (str, int)):
        owner = chat.get("owner")
    link = chat.get("link")
    return {
        "chat_is_dict": True,
        "chat_type": str(chat_type or "").lower() or None,
        "owner_present": owner is not None,
        "owner_type": type(owner).__name__ if owner is not None else None,
        "initiator_present": bool(initiator_user_id),
        "owner_matches_initiator": bool(owner and initiator_user_id and str(owner) == str(initiator_user_id)),
        "link_present": bool(link),
        "link_type": type(link).__name__ if link is not None else None,
        "link_valid": bool(valid_max_target(link)),
    }


def official_bot_added_channel(chat: dict, initiator_user_id: str | None) -> dict | None:
    """Validate the official channel metadata used by ``bot_added``.

    ``bot_added.user`` is the authorized connector signal for this flow. MAX
    does not guarantee ``owner_id`` for private channels. The link is accepted
    only from the official chat response; user-provided URLs never enter this
    flow.
    """
    if not isinstance(chat, dict) or not initiator_user_id:
        return None
    chat_type = chat.get("chat_type") or chat.get("type")
    if str(chat_type or "").lower() != "channel":
        return None
    link = chat.get("link")
    if not valid_max_target(link):
        return None
    return {
        "owner_user_id": str(initiator_user_id),
        "title": str(chat.get("title") or chat.get("name") or "Канал MAX"),
        "avatar_url": chat.get("icon") if isinstance(chat.get("icon"), str) else None,
        "target_url": str(link),
        "is_public": bool(chat.get("is_public", chat.get("public", False))),
        "metadata": json.dumps({
            "title": chat.get("title") or chat.get("name"),
            "icon_present": bool(chat.get("icon")),
            "is_public": bool(chat.get("is_public", chat.get("public", False))),
            "link_present": True,
            "owner_id_present": bool(chat.get("owner_id") or chat.get("owner_user_id") or chat.get("owner")),
            "participants_count": chat.get("participants_count") if isinstance(chat.get("participants_count"), int) else None,
        }, ensure_ascii=False),
    }


async def render_screen(db: AsyncSession, admin: Admin, screen: str, channel_id: str | None = None) -> tuple[str, list[list[dict]]]:
    channels = (await db.scalars(select(Channel).where(Channel.admin_id == admin.id).order_by(Channel.title))).all()
    if screen == "home":
        # The current project has no persisted custom-bot entity yet; the
        # existing "Мои боты" screen therefore has zero connected bots.
        # Keep this count isolated so a future bot repository can replace
        # only this value without changing the home UI.
        connected_bots = int(await db.scalar(select(func.count(BotConnection.id)).where(BotConnection.owner_admin_id == admin.id)) or 0)
        return menu_text(len(channels), connected_bots), buttons(
            ("📢 Подключить канал", "connect_channel"),
            ("📣 Продвижение", "promotion"),
            ("📩 Обращения / Предложка", "channels"),
            ("🤖 Мои боты", "my_bots"),
            ("ℹ️ О сервисе", "about"),
        )
    if screen == "connect_channel":
        return channel_connection_instructions(), buttons(("⬅️ Главное меню", "home"))
    if screen == "promotion":
        if not channels:
            return channel_connection_instructions(), buttons(("⬅️ Назад", "home"))
        rows = [[(f"📢 {channel.title}", f"promotion_channel:{channel.id}")] for channel in channels]
        rows.extend([[ ("📣 Подключить ещё", "promotion_connect") ], [("⬅️ Назад", "home")]])
        return promotion_intro_text(), buttons(*rows)
    if screen == "promotion_connect":
        return promotion_connect_text(), buttons(("⬅️ Назад", "promotion"))
    if screen.startswith("promotion_channel:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel:
            return await render_screen(db, admin, "promotion")
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id))
        landing = await db.scalar(select(LandingPage).where(
            LandingPage.channel_id == channel.id,
            LandingPage.is_active.is_(True),
        ))
        yandex = await connection_status(admin.external_id, channel.id)
        metrica = await channel_setup_status(admin.external_id, channel.id)
        yandex_value = metrica.get("owner_login") or ("Подключён" if yandex else None)
        counter_id = (profile.yandex_counter_id if profile else None) or metrica.get("counter_id")
        return (
            promotion_channel_summary(
                channel.title,
                yandex_value,
                counter_id,
                public_landing_url(landing.public_id) if landing else None,
            ),
            buttons(("📄 Лендинг", f"promotion_landing:{channel.id}"),
                    ("⚡ Яндекс", f"promotion_yandex:{channel.id}"),
                    ("📊 Аналитика", f"promotion_analytics:{channel.id}"), ("⚙️ Настройки", f"promotion_settings:{channel.id}"),
                    ("⬅️ Назад", "promotion")),
        )
    if screen.startswith("promotion_landing:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel:
            return await render_screen(db, admin, "promotion")
        landing = await db.scalar(select(LandingPage).where(LandingPage.channel_id == selected_id, LandingPage.is_active.is_(True)))
        if not landing:
            return (f"📄 Лендинг\n\nДля канала «{channel.title}» лендинг ещё не создан.\nОткройте раздел ещё раз после создания лендинга.",
                    buttons(("⬅️ Назад", f"promotion_channel:{channel.id}")))
        url = public_landing_url(landing.public_id)
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == selected_id))
        target_hint = "✅ Цель перехода настроена." if profile and profile.target_url else "⚠️ Ссылка закрытого MAX-канала пока не настроена."
        return (f"📄 Лендинг\n\n📢 {channel.title}\n🟢 Активен\n\n🔗 {url}\n\n{target_hint}",
                [[{"text": "🌐 Открыть лендинг", "type": "link", "url": url}],
                 [{"text": "⚙️ Настроить", "callback_data": f"promotion_settings:{channel.id}"}],
                 [{"text": "⬅️ Назад", "callback_data": f"promotion_channel:{channel.id}"}]])
    if screen.startswith("promotion_analytics:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel:
            return await render_screen(db, admin, "promotion")
        stats = await traffic_stats(db, selected_id)
        return (f"📊 Аналитика\n\n📢 {channel.title}\n\n"
                f"👀 Посещений лендинга: {stats['visits']}\n"
                f"📢 Перешли в MAX: {stats['to_channel']}\n👥 Всего новых участников: {stats['new_members']}\n"
                f"↩️ Отписок: {stats['unsubscribed']}\n\nMAX signals:\n"
                f"MAX_OPEN_LIKELY: {stats['max_open_likely']}\nMAX_OPEN_CONFIRMED: {stats['max_open_confirmed']}\n"
                f"JOIN_REQUEST: {stats['join_events']}\n\nАтрибуция:\n"
                f"EXACT: {stats['EXACT']}\nHIGH: {stats['HIGH']}\n"
                f"MEDIUM: {stats['MEDIUM']}\nAMBIGUOUS: {stats['AMBIGUOUS']}\nUNATTRIBUTED: {stats['UNATTRIBUTED']}\n\n"
                "В Яндекс отправляются только EXACT/HIGH; отправка по умолчанию в dry-run.",
                buttons(("⬅️ Назад", f"promotion_channel:{channel.id}")))
    if screen.startswith("promotion_yandex:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel:
            return await render_screen(db, admin, "promotion")
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == selected_id))
        oauth_configured = configured()
        status_data = await connection_status(admin.external_id, selected_id) if oauth_configured else None
        metrica = await channel_setup_status(admin.external_id, selected_id)
        counter_id = (profile.yandex_counter_id if profile else None) or metrica.get("counter_id")
        conversions = int(await db.scalar(
            select(func.count(OfflineConversion.id))
            .join(TrafficMembership, OfflineConversion.membership_id == TrafficMembership.id)
            .where(TrafficMembership.channel_id == selected_id)
        ) or 0)
        text = promotion_yandex_summary(channel.title, bool(status_data), counter_id, conversions)
        if status_data:
            if oauth_configured:
                state = await create_state(admin.external_id, selected_id)
                return (
                    text,
                    [
                        [{"text": "🔄 Переподключить Яндекс", "type": "link", "url": authorize_url(state)}],
                        [{"text": "⬅️ Назад", "callback_data": f"promotion_channel:{channel.id}"}],
                    ],
                )
            return (text, buttons(("⬅️ Назад", f"promotion_channel:{channel.id}")))
        if oauth_configured:
            state = await create_state(admin.external_id, selected_id)
            return (text,
                    [[{"text": "🔐 Подключить Яндекс", "type": "link", "url": authorize_url(state)}],
                     [{"text": "⬅️ Назад", "callback_data": f"promotion_channel:{channel.id}"}]])
        logger.warning("YANDEX_OAUTH_UI_UNAVAILABLE channel_id=%s", channel.id)
        return (
            f"{text}\n\nИнтеграция временно недоступна. Обратитесь к администратору.",
            buttons(("⬅️ Назад", f"promotion_channel:{channel.id}")),
        )
    if screen.startswith("promotion_settings:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel:
            return await render_screen(db, admin, "promotion")
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == selected_id))
        target = profile.target_url if profile else None
        target_line = target or "⚠️ Native invite этого канала не обнаружен автоматически."
        landing = await db.scalar(select(LandingPage).where(LandingPage.channel_id == selected_id, LandingPage.is_active.is_(True)))
        readiness = "✅ Landing готов к переходу" if landing and valid_max_target(target) else "⛔ Readiness заблокирован: automatic target discovery недоступен"
        return (f"⚙️ Настройки продвижения\n\n📢 {channel.title}\n\n"
                f"🎯 Native invite только этого канала:\n{target_line}\n\n{readiness}\n"
                "Автоматическое получение invite через текущий официальный Bot API не реализовано: API-клиент не предоставляет invite-link метода."
                " MAX Web/CDP discovery требует отдельной авторизованной сессии владельца и пока не доказана."
                " Пользовательские URL не запрашиваются; fallback и чужие каналы не используются.",
                buttons(("🔄 Проверить readiness", f"promotion_settings:{channel.id}"),
                        ("⬅️ Назад", f"promotion_channel:{channel.id}")))
    if screen.startswith("promotion_target_prompt:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel:
            return await render_screen(db, admin, "promotion")
        return (f"🎯 Автоматическое обнаружение target\n\n📢 {channel.title}\n\n"
                "Ручной ввод invite отключён. Официальный MAX Bot API в текущем клиенте не предоставляет метод invite-link;"
                " Web/CDP discovery владельческой сессии ещё не доказана.\n\n"
                "Статус: AUTO_TARGET_DISCOVERY=BLOCKED",
                buttons(("⬅️ Назад", f"promotion_settings:{channel.id}")))
    if screen == "about":
        return (
            "ℹ️ О MAXIBOT\n\n"
            "MAXIBOT помогает владельцам каналов MAX принимать обращения подписчиков "
            "и отвечать на них из отдельных рабочих чатов.\n\n"
            "Можно использовать системного MAXIBOT или подключить собственного бота "
            "с вашим названием и аватаром.\n\n"
            "Если возник вопрос по подключению или работе сервиса — напишите в поддержку.",
            buttons(("💬 Написать в поддержку", "support_start"), ("⬅️ Назад", "home")),
        )
    if screen == "owner_admin":
        stats = await owner_admin_stats(db)
        return (
            "🔐 MAXIBOT · Администратор\n\n"
            f"👤 Клиентов всего: {stats['clients_total']}\n"
            f"🆕 Новых сегодня: {stats['clients_today']}\n\n"
            f"📋 Подключено каналов: {stats['channels_total']}\n"
            f"🤖 Подключено своих ботов: {stats['custom_bots_total']}\n\n"
            f"🎫 Открытых тикетов: {stats['open_tickets']}",
            buttons(("⬅️ Назад", "home")),
        )
    if screen == "support_prompt":
        return (
            "💬 Поддержка MAXIBOT\n\n"
            "Опишите ваш вопрос одним сообщением.\n\n"
            "Можно отправить текст, фото, видео или файл.\n\n"
            "Мы получим обращение и ответим вам здесь.",
            buttons(("❌ Отмена", "support_cancel")),
        )
    if screen.startswith("support_existing:"):
        ticket_id = screen.split(":", 1)[1]
        ticket = await db.scalar(select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.max_user_id == admin.external_id))
        if not ticket:
            return "ℹ️ О MAXIBOT", buttons(("💬 Написать в поддержку", "support_start"), ("⬅️ Назад", "home"))
        return (
            f"🎫 У вас уже есть открытое обращение #{ticket.display_number}.\n\n"
            "Вы можете дополнить его новым сообщением.",
            buttons(("✍️ Дополнить обращение", f"support_continue:{ticket.id}"), ("⬅️ Назад", "about")),
        )
    if screen == "messages:global":
        return "💬 Сообщения\n\nℹ️ Зажмите нужное сообщение → «Ответить».", []
    if screen.startswith("channel_added:"):
        selected_id = screen.split(":", 1)[1] if ":" in screen else None
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id)) if selected_id else None
        if not channel:
            return "Канал добавлен.", buttons(("📋 Мои каналы", "channels"), ("⬅️ Главное меню", "home"))
        return (
            f"✅ Канал **«{channel.title}»** успешно добавлен!\n\n📋 Ваши каналы: {len(channels)}",
            buttons(("📋 Мои каналы", "channels"), ("📣 Подключить ещё", "add_channel"), ("⬅️ Главное меню", "home")),
        )
    if screen.startswith("delete_confirm:"):
        selected_id = screen.split(":", 1)[1] if ":" in screen else None
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id)) if selected_id else None
        if not channel:
            return "Канал уже удалён.", buttons(("📋 Мои каналы", "channels"))
        return (
            "🗑 Удаление канала\n\n"
            f"Вы действительно хотите удалить\n«{channel.title}» из MAXIBOT?\n\n"
            "Будут удалены:\n\n"
            "• подключение канала;\n"
            "• подключение чата для обращений;\n"
            "• история обращений этого канала в MAXIBOT;\n"
            "• все внутренние привязки и настройки.\n\n"
            "MAXIBOT также покинет канал и чат для обращений.\n\n"
            "Сам канал и групповой чат в MAX останутся.",
            buttons(
                ("🗑 Да, удалить", f"delete_channel_confirm:{channel.id}"),
                ("❌ Отмена", f"delete_cancel:{channel.id}"),
            ),
        )
    if screen.startswith("channel_deleted:"):
        title = unquote(screen.split(":", 1)[1]) if ":" in screen else "Канал"
        return (
            f"✅ Канал «{title}» удалён из MAXIBOT.\n\n"
            "MAXIBOT больше не подключён к каналу\n"
            "и чату для обращений.",
            buttons(("📋 Мои каналы", "channels")),
        )
    if screen.startswith("channel_delete_failed:"):
        title = unquote(screen.split(":", 1)[1]) if ":" in screen else "канала"
        return (
            f"⚠️ Не удалось удалить «{title}».\n\n"
            "MAXIBOT не изменил подключение. Повторите попытку позже.",
            buttons(("📋 Мои каналы", "channels")),
        )
    if screen.startswith("template_saved:") or screen.startswith("template_view:"):
        parts = screen.split(":")
        selected_id = parts[1] if len(parts) > 1 else None
        template_type = parts[2] if len(parts) > 2 else "welcome"
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id)) if selected_id else None
        if not channel or template_type not in ("welcome", "sent"):
            return "Настройка недоступна.", buttons(("⬅️ Назад", f"settings:{selected_id}" if selected_id else "channels"))
        title = "👋 Приветственное сообщение" if template_type == "welcome" else "✅ Уведомление об отправке"
        explanation = ("Пользователь увидит его после перехода\nпо персональной ссылке канала."
                       if template_type == "welcome" else
                       "Пользователь увидит его после того,\nкак обращение успешно отправлено администратору.")
        current = (normalize_channel_welcome(channel.welcome_text)
                   if template_type == "welcome" else normalize_channel_sent(channel.sent_text))
        notice = "✅ Сообщение обновлено.\n\n" if screen.startswith("template_saved:") else ""
        return (f"{notice}{title}\n\n{explanation}\n\nТекущий текст:\n\n{current}", buttons(
            ("✏️ Изменить", f"template:{selected_id}:{template_type}"),
            ("♻️ По умолчанию", f"reset_template:{selected_id}:{template_type}"),
            ("⬅️ Назад", f"settings:{selected_id}"),
        ))
    if screen.startswith("template_prompt:"):
        parts = screen.split(":")
        selected_id = parts[1] if len(parts) > 1 else None
        template_type = parts[2] if len(parts) > 2 else "welcome"
        current = await db.scalar(select(Channel.welcome_text if template_type == "welcome" else Channel.sent_text).where(Channel.id == selected_id, Channel.admin_id == admin.id)) if selected_id else None
        if current is None:
            return "Настройка недоступна.", buttons(("⬅️ Назад", f"settings:{selected_id}" if selected_id else "channels"))
        return (
            "✏️ Отправьте новый текст сообщения.",
            buttons(("❌ Отмена", f"cancel_template:{selected_id}")),
        )
    if screen == "add_channel":
        return channel_connection_instructions(), buttons(("⬅️ Назад", "channels"))
    if screen == "channels":
        if not channels:
            return channel_connection_instructions(), buttons(("⬅️ Назад", "home"))
        rows = [[(
            f"{'🔴' if not channel.accepting else '🟢' if channel.inbox_chat_id else '🟡'} {channel.title}",
            f"channel:{channel.id}",
        )] for channel in channels]
        rows.extend([[("📣 Подключить ещё", "add_channel")], [("⬅️ Назад", "home")]])
        return ("📩 Обращения / Предложка\n\nЗдесь ваши подключённые каналы.\n"
                "Выберите канал для управления\nили добавьте новый."), buttons(*rows)
    if screen.startswith("channel:") or screen == "channel":
        selected_id = channel_id or (screen.split(":", 1)[1] if ":" in screen else None)
        channel = await db.scalar(select(Channel).options(selectinload(Channel.conversations)).where(Channel.id == selected_id, Channel.admin_id == admin.id)) if selected_id else (channels[0] if channels else None)
        if not channel:
            return await render_screen(db, admin, "channels")
        custom_bot = None
        if channel.communication_mode == "CUSTOM_BOT" and channel.bot_connection_id:
            custom_bot = await db.scalar(select(BotConnection).where(
                BotConnection.id == channel.bot_connection_id,
                BotConnection.owner_admin_id == admin.id,
                BotConnection.status == "ACTIVE",
            ))
        custom_bot_ready = bool(custom_bot and custom_bot.admin_private_chat_id)
        if not channel.accepting:
            status = "Приём сообщений отключён"
            status_icon = "🔴"
        elif channel.inbox_chat_id or custom_bot_ready:
            status = "Принимает сообщения"
            status_icon = "🟢"
        else:
            status = "Настройка не завершена"
            status_icon = "🟡"
        toggle = ("⛔ Отключить приём сообщений", f"receiving:{channel.id}:off") if channel.accepting and channel.inbox_chat_id else ("✅ Включить приём сообщений", f"receiving:{channel.id}:on")
        if channel.inbox_chat_id:
            inbox_status = f"🟢 {channel.inbox_chat_title or channel.inbox_chat_id}"
        elif custom_bot_ready:
            inbox_status = "🟢 Персональный бот подключён"
        else:
            inbox_status = "🔴 Не подключён"
        inbox_action = ("🔄 Сменить чат", f"inbox_change:{channel.id}") if channel.inbox_chat_id else ("💬 Подключить чат", f"inbox_connect:{channel.id}")
        personal_link_block = (
            f"🔗 Персональная ссылка:\n{personal_link(channel.public_slug)}"
            if channel.inbox_chat_id or custom_bot_ready
            else "🔗 Персональная ссылка:\n🟡 Подключите чат ниже, чтобы создать ссылку"
        )
        text = (f"📕 Канал: {channel.title}\n"
                f"{status_icon} Статус: {status}\n\n"
                f"💬 Чат для обращений:\n{inbox_status}\n\n"
                f"{personal_link_block}")
        return text, buttons(
            (inbox_action[0], inbox_action[1]),
            *((toggle,) if channel.inbox_chat_id else ()),
            ("⚙️ Настройка сообщений пользователя", f"settings:{channel.id}"),
            ("🗑 Удалить канал", f"delete:{channel.id}"),
            ("⬅️ Назад", "channels"),
        )
    if screen.startswith("inbox_connect:") or screen.startswith("inbox_change:"):
        selected_id = screen.split(":", 1)[1] if ":" in screen else None
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id)) if selected_id else None
        if not channel:
            return await render_screen(db, admin, "channels")
        if screen.startswith("inbox_change:"):
            return (
                "🔄 Смена чата\n\n"
                f"Сейчас к каналу «{channel.title}» подключён чат:\n\n"
                f"💬 {channel.inbox_chat_title or channel.inbox_chat_id}\n\n"
                "Чтобы заменить его:\n\n"
                "1️⃣ Создайте новый групповой чат в MAX\n"
                "или выберите другой существующий чат.\n\n"
                "2️⃣ Добавьте в него MAXIBOT.\n\n"
                "3️⃣ Назначьте MAXIBOT администратором\n"
                "и включите право «Читать сообщения».\n\n"
                "После подключения новый чат заменит текущий.\n"
                "Все новые обращения будут приходить уже в него.\n\n"
                "Старый чат останется в MAX, но MAXIBOT больше\n"
                "не будет отправлять туда новые обращения.\n\n"
                "⏳ Ожидаю добавления MAXIBOT в новый чат...",
                buttons(("❌ Отмена", f"inbox_cancel:{channel.id}")),
            )
        return (
            "💬 Подключение чата\n\n"
            f"Чтобы получать обращения пользователей канала\n«{channel.title}»:\n\n"
            "1️⃣ Создайте обычный групповой чат в MAX.\n\n"
            f"Например:\n«Обращения — {channel.title}»\n\n"
            "2️⃣ Добавьте в этот чат MAXIBOT.\n\n"
            "3️⃣ Назначьте MAXIBOT администратором\n"
            "и включите право «Читать сообщения».\n\n"
            "После этого ничего отправлять в чат не нужно —\n"
            f"MAXIBOT автоматически обнаружит его\nи подключит к каналу «{channel.title}».\n\n"
            "⏳ Ожидаю добавления MAXIBOT в чат...",
            buttons(("⬅️ Назад", f"inbox_cancel:{channel.id}")),
        )
    if screen.startswith("feed_header:"):
        selected_id = screen.split(":", 1)[1] if ":" in screen else None
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id)) if selected_id else None
        if not channel:
            return await render_screen(db, admin, "channels")
        return f"💬 Сообщения — {channel.title}\nℹ️ Зажмите сообщение → «Ответить»." , []
    if screen.startswith("messages:"):
        selected_id, page = _screen_args(screen, channel_id)
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id)) if selected_id else None
        if not channel:
            return await render_screen(db, admin, "channels")
        # This screen is a single channel-wide chronological feed, not a
        # conversation directory. Page zero contains the newest messages;
        # older pages are reached with "⬆️ Ранее".
        messages = list((await db.scalars(
            select(Message)
            .join(Conversation)
            .where(Conversation.channel_id == channel.id)
            .options(selectinload(Message.conversation))
            .order_by(Message.created_at.desc(), Message.id.desc())
        )).all())
        for conversation in (await db.scalars(select(Conversation).where(Conversation.channel_id == channel.id))).all():
            conversation.unread = 0
        await db.commit()
        # MAX limits message text, so use a text budget instead of a fixed
        # message count. Offset is the number of newer messages skipped.
        text_budget = 3500

        def message_lines(message: Message) -> list[str]:
            conversation = message.conversation
            sender = (f"🟦 Вы → {conversation.user_name}" if message.direction == "out"
                      else f"{_author_color(conversation.user_external_id)} {conversation.user_name}")
            return [f"{sender} · {_time(message.created_at)}", message.text or "Фото", ""]

        def fitting_count(start: int) -> int:
            used = len("💬 Сообщения\n📕 ") + len(channel.title) + 2
            count = 0
            # Messages are stored newest-first here; the selected block is
            # reversed only when rendered so the screen remains old → new.
            for message in messages[start:]:
                addition = sum(len(line) + 1 for line in message_lines(message))
                if count and used + addition > text_budget:
                    break
                count += 1
                used += addition
            return max(1, count) if start < len(messages) else 0

        # Reconstruct the dynamic page boundary from the beginning so the
        # "Новее" button can point to the previous variable-size block.
        offset = max(0, min(page, len(messages)))
        page_starts = [0]
        cursor = 0
        while cursor < len(messages):
            cursor += fitting_count(cursor)
            if cursor < len(messages):
                page_starts.append(cursor)
        if offset not in page_starts:
            offset = min(page_starts, key=lambda value: abs(value - offset))
        count = fitting_count(offset)
        newest_first = messages[offset:offset + count]
        visible = list(reversed(newest_first))
        lines = [f"💬 Сообщения — {channel.title}"]
        rows = []
        authors: list[tuple[str, str]] = []
        author_ids: set[str] = set()
        for message in visible:
            conversation = message.conversation
            if message.direction == "out":
                sender = f"🟦 Вы → {conversation.user_name}"
            else:
                sender = f"🔵 {conversation.user_name}"
            content = message.text or "Фото"
            lines.extend([f"{sender} · {_time(message.created_at)}", content, ""])
            if message.direction != "out" and conversation.id not in author_ids:
                author_ids.add(conversation.id)
                authors.append((conversation.user_name, conversation.id))
        if not visible:
            lines.append("\nНет сообщений.")
        # One reply button per unique incoming author on this page.
        for offset in range(0, len(authors), 2):
            rows.append([
                (f"✍️ {name}", f"reply:{conversation_id}")
                for name, conversation_id in authors[offset:offset + 2]
            ])
        pager = []
        if offset + count < len(messages):
            pager.append(("⬆️ Ранее", f"messages:{channel.id}:{offset + count}"))
        if offset > 0:
            previous_offset = page_starts[page_starts.index(offset) - 1]
            pager.append(("⬇️ Новее", f"messages:{channel.id}:{previous_offset}"))
        if pager:
            rows.append(pager)
        rows.append([("⬅️ Назад", f"channel:{channel.id}")])
        return "\n".join(lines), buttons(*rows)
    if screen.startswith("conversation:"):
        conversation_id, page = _screen_args(screen, channel_id)
        conversation = await db.scalar(select(Conversation).options(selectinload(Conversation.channel), selectinload(Conversation.messages)).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == admin.id)) if conversation_id else None
        if not conversation:
            return "Пользователь не найден.", buttons(("⬅️ К сообщениям", "channels"))
        conversation.unread = 0
        await db.commit()
        messages = list(conversation.messages)
        page_size = 6
        page_count = max(1, (len(messages) + page_size - 1) // page_size)
        page = max(0, min(page, page_count - 1))
        start = max(0, len(messages) - (page + 1) * page_size)
        end = len(messages) - page * page_size
        visible = messages[start:end]
        lines = [f"👤 {conversation.user_name}", f"📕 {conversation.channel.title}"]
        for message in visible:
            who = "🟦 Вы" if message.direction == "out" else "👤"
            lines.extend([f"{who} · {_time(message.created_at)}", message.text or "Фото", ""])
        if not visible:
            lines.append("Нет сообщений.")
        buttons_rows = [[("✍️ Ответить", f"reply:{conversation.id}")]]
        if page + 1 < page_count:
            buttons_rows.append([("⬆️ Ранее", f"conversation:{conversation.id}:{page + 1}")])
        else:
            buttons_rows.append([("⬆️ Ранее", f"conversation:{conversation.id}:{page}")])
        blocked = await db.scalar(select(func.count()).select_from(BlockedUser).where(BlockedUser.channel_id == conversation.channel_id, BlockedUser.user_external_id == conversation.user_external_id))
        buttons_rows.append([("✅ Разблокировать" if blocked else "🚫 Заблокировать", f"unblock:{conversation.id}" if blocked else f"block:{conversation.id}")])
        buttons_rows.append([("⬅️ К сообщениям", f"messages:{conversation.channel_id}:0")])
        return "\n".join(lines).rstrip(), buttons(*buttons_rows)
    if screen.startswith("reply:"):
        conversation_id = screen.split(":", 1)[1] if ":" in screen else None
        conversation = await db.scalar(select(Conversation).options(selectinload(Conversation.channel)).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == admin.id)) if conversation_id else None
        if not conversation:
            return "Пользователь не найден.", buttons(("⬅️ К сообщениям", "channels"))
        return f"✍️ Ответ {conversation.user_name}\n📕 {conversation.channel.title}\n\nОтправьте сообщение.", buttons(("❌ Отмена", f"cancel_reply:{conversation.id}"))
    if screen.startswith("settings"):
        selected_id = channel_id or (screen.split(":", 1)[1] if ":" in screen else None)
        return ("⚙️ Настройка сообщений пользователя\n\nВыберите, какой текст нужно изменить."), buttons(
            ("1️⃣ Приветственное сообщение", f"template_view:{selected_id}:welcome"),
            ("2️⃣ Уведомление об отправке", f"template_view:{selected_id}:sent"),
            ("⬅️ Назад", f"channel:{selected_id}" if selected_id else "channels"))
    if screen == "bots":
        return (
            "🤖 Боты\n\nУправляйте подключёнными ботами или добавьте нового.",
            buttons(("🤖 Мои боты", "my_bots"), ("➕ Добавить бота", "connect_bot"), ("⬅️ Назад", "home")),
        )
    if screen == "my_bots":
        bot_rows = (await db.scalars(select(BotConnection).where(BotConnection.owner_admin_id == admin.id).order_by(BotConnection.display_name))).all()
        if not bot_rows:
            return ("🤖 Мои боты\n\n"
                    "Подключите собственного MAX-бота со своим названием, аватаром и оформлением.\n\n"
                    "Пользователи будут писать именно вашему боту, а все их сообщения будут приходить вам прямо в этот же бот. Отвечать можно там же — без перехода в MAXIBOT.\n\n"
                    "Такой бот можно использовать для обратной связи, поддержки клиентов, приёма вопросов и обращений.\n\n"
                    "MAXIBOT работает в фоне и обеспечивает доставку сообщений между вами и пользователями.\n\n"
                    "ℹ️ Важно: создать собственного бота в MAX могут юридические лица, ИП и самозанятые — резиденты РФ.\n\n"
                    "У вас пока нет подключённых ботов.",
                    buttons(("➕ Подключить своего бота", "connect_bot"), ("⬅️ Назад", "home")))
        rows = []
        for bot in bot_rows:
            icon = "🟢" if bot.status == "ACTIVE" else "⚫" if bot.status == "DISABLED" else "🔴" if bot.status in ("ERROR", "INVALID_TOKEN") else "🟡"
            username = f"@{bot.username.lstrip('@')}"
            display_name = (bot.display_name or "").strip()
            if len(display_name) > 32:
                display_name = f"{display_name[:29].rstrip()}..."
            label = f"{display_name} · {username}" if display_name else username
            rows.append([(f"{icon} {label}", f"custom_bot:{bot.id}")])
        rows.extend([[("➕ Добавить бота", "connect_bot")], [("⬅️ Назад", "home")]])
        return ("🤖 Мои боты\n\nВыберите бота для управления:", buttons(*rows))
    if screen == "connect_bot_intro":
        return ("🤖 Мои боты\n\n"
                "Подключите собственного MAX-бота со своим названием, аватаром и оформлением.\n\n"
                "Пользователи будут писать именно вашему боту, а все их сообщения будут приходить вам прямо в этот же бот. Отвечать можно там же — без перехода в MAXIBOT.\n\n"
                "Такой бот можно использовать для обратной связи, поддержки клиентов, приёма вопросов и обращений.\n\n"
                "MAXIBOT работает в фоне и обеспечивает доставку сообщений между вами и пользователями.\n\n"
                "ℹ️ Важно: создать собственного бота в MAX могут юридические лица, ИП и самозанятые — резиденты РФ.",
                buttons(("✅ Продолжить подключение", "custom_bot_token"), ("⬅️ Назад", "my_bots")))
    if screen == "connect_bot":
        return ("🤖 Добавить своего бота\n\n"
                "Перейдите на MAX для бизнеса — business.max.ru\n"
                "Откройте раздел «Чат-боты».\n"
                "Создайте бота и получите токен.\n"
                "Вернитесь сюда и отправьте токен следующим сообщением.",
                [[{"text": "🌐 Открыть MAX для бизнеса", "type": "link", "url": "https://business.max.ru/"}],
                 [{"text": "⬅️ Назад", "callback_data": "my_bots"}]])
    if screen == "custom_bot_token":
        return ("🤖 Добавить своего бота\n\n"
                "Перейдите на MAX для бизнеса — business.max.ru\n"
                "Откройте раздел «Чат-боты».\n"
                "Создайте бота и получите токен.\n"
                "Вернитесь сюда и отправьте токен следующим сообщением.",
                [[{"text": "🌐 Открыть MAX для бизнеса", "type": "link", "url": "https://business.max.ru/"}],
                 [{"text": "⬅️ Назад", "callback_data": "my_bots"}]])
    if screen == "custom_bot_refresh_token":
        return ("🔑 Обновить токен\n\n"
                "Отправьте новый токен этого бота одним сообщением.\n\n"
                "После проверки новый токен заменит текущий.\n"
                "Если передумали, нажмите «Отмена».",
                [[{"text": "❌ Отмена", "callback_data": "custom_bot_refresh_cancel"}]])
    if screen == "custom_bot_help":
        return ("📖 Как создать бота в MAX\n\n"
                "1. Откройте официальный интерфейс создания ботов MAX.\n"
                "2. Создайте нового бота.\n"
                "3. Укажите название и оформление.\n"
                "4. Получите токен бота.\n"
                "5. Вернитесь в MAXIBOT и отправьте токен в раздел «Добавить бота».\n\n"
                "После подключения пользователи смогут писать вашему боту, а их сообщения будут приходить вам прямо в этот же бот.",
                buttons(("⬅️ Назад", "custom_bot_token")))
    if screen.startswith("custom_bot_found:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot: return "Бот не найден.", buttons(("⬅️ Назад", "my_bots"))
        return (f"✅ Бот найден\n\n🤖 {bot.display_name}\n@{bot.username}\n\nЧтобы завершить подключение, один раз откройте своего бота и нажмите «Начать».", [[{"text": "🤖 Открыть бота", "type": "link", "url": f"https://max.ru/{bot.username.lstrip('@')}"}], [{"text": "❌ Отмена", "callback_data": "my_bots"}]])
    if screen.startswith("custom_bot_error"):
        return ("❌ Не удалось подключить бота.\n\n"
                "Проверьте токен и отправьте его ещё раз.\n\n"
                "⬇️ Отправьте новый токен прямо в этот чат.",
                [[{"text": "🌐 Открыть MAX для бизнеса", "type": "link", "url": "https://business.max.ru/"}],
                 [{"text": "❌ Отмена", "callback_data": "custom_bot_cancel"}]])
    if screen == "custom_bot_activation_error":
        return ("❌ Не удалось автоматически подключить бота.\n\n"
                "Попробуйте повторить подключение позже. Если ошибка повторится, обратитесь в поддержку.",
                buttons(("🤖 Мои боты", "my_bots")))
    if screen.startswith("custom_bot_active:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot: return "Бот не найден.", buttons(("⬅️ Назад", "my_bots"))
        return (f"✅ Бот подключён\n\n🤖 {bot.display_name}\n@{bot.username}\n\nВаш бот готов к работе.\n\n🔗 Ссылка для пользователей:\nhttps://max.ru/{bot.username.lstrip('@')}", buttons(("🤖 Мои боты", "my_bots")))
    if screen.startswith("custom_bot:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot: return "Бот не найден.", buttons(("⬅️ Назад", "my_bots"))
        username = bot.username.lstrip('@')
        if bot.status == "DISABLED":
            return (f"🤖 Управление ботом\n\n{bot.display_name}\n@{username}\n\n⚫ Статус: Отключён.",
                    buttons(("▶️ Включить бота", f"custom_bot_enable:{bot.id}"), ("🗑 Удалить бота", f"custom_bot_delete:{bot.id}"), ("⬅️ Мои боты", "my_bots")))
        if bot.status in ("ERROR", "INVALID_TOKEN"):
            return (f"🤖 Управление ботом\n\n{bot.display_name}\n@{username}\n\n🔴 Статус: Ошибка токена.", buttons(("🔑 Обновить токен", f"custom_bot_refresh:{bot.id}"), ("🗑 Удалить бота", f"custom_bot_delete:{bot.id}"), ("⬅️ Мои боты", "my_bots")))
        if bot.status != "ACTIVE":
            return (f"🤖 Управление ботом\n\n{bot.display_name}\n@{username}\n\n🟡 Статус: Настройка не завершена\n\nОткройте своего бота от аккаунта владельца и нажмите «Начать».", [[{"text": "🤖 Открыть бота", "type": "link", "url": f"https://max.ru/{username}"}], [{"text": "🗑 Удалить бота", "callback_data": f"custom_bot_delete:{bot.id}"}], [{"text": "⬅️ Мои боты", "callback_data": "my_bots"}]])
        return (f"🤖 Управление ботом\n\n{bot.display_name}\n@{username}\n\n🟢 Статус: Работает", buttons(("✏️ Тексты", f"custom_bot_texts:{bot.id}"), ("📣 Рассылка", f"custom_bot_broadcast:{bot.id}"), ("📊 Статистика", f"custom_bot_stats:{bot.id}"), ("🔑 Обновить токен", f"custom_bot_refresh:{bot.id}"), ("⛔ Отключить бота", f"custom_bot_disable:{bot.id}"), ("⬅️ Мои боты", "my_bots")))
    if screen.startswith("custom_bot_settings:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot: return "Бот не найден.", buttons(("⬅️ Назад", "my_bots"))
        return (f"✏️ Тексты\n\n👋 Приветственное сообщение:\n{bot.welcome_text}\n\n✅ Уведомление об отправке:\n{bot.sent_text}", buttons(("1️⃣ Приветственное сообщение", f"custom_bot_template:{bot.id}:welcome"), ("2️⃣ Уведомление об отправке", f"custom_bot_template:{bot.id}:sent"), ("⬅️ Назад", f"custom_bot:{bot.id}")))
    if screen.startswith("custom_bot_texts:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot:
            return "Бот не найден.", buttons(("⬅️ Мои боты", "my_bots"))
        return (
            "✏️ Тексты\n\n"
            "Здесь вы можете изменить тексты сообщений, которые пользователи получают в вашем боте.\n\n"
            "Русский установлен по умолчанию. Этот язык используется при первом запуске бота.",
            buttons(
                ("✏️ Изменить текст приветствия", f"custom_bot_template_view:{bot.id}:welcome"),
                ("✏️ Изменить автоответ", f"custom_bot_template_view:{bot.id}:sent"),
                ("⬅️ Назад", f"custom_bot:{bot.id}"),
            ),
        )
    if screen.startswith("custom_bot_dialogs:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot:
            return "Бот не найден.", buttons(("⬅️ Мои боты", "my_bots"))
        messages = list((await db.scalars(select(CustomBotMessage).where(CustomBotMessage.bot_connection_id == bot.id).order_by(CustomBotMessage.created_at))).all())
        latest: dict[str, CustomBotMessage] = {}
        for message in messages:
            latest[message.source_user_id] = message
        rows = [(f"👤 {message.source_user_id} · {message.text or 'Вложение'}", f"custom_bot_dialog:{bot.id}:{user_id}") for user_id, message in latest.items()]
        return (f"💬 Диалоги\n\nВыберите диалог:" if rows else f"💬 Диалоги\n\nДиалогов пока нет.",
                buttons(*rows, ("⬅️ Назад", f"custom_bot:{bot.id}")) if rows else buttons(("⬅️ Назад", f"custom_bot:{bot.id}")))
    if screen.startswith("custom_bot_stats:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot:
            return "Бот не найден.", buttons(("⬅️ Мои боты", "my_bots"))
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        total_users = int(await db.scalar(select(func.count(func.distinct(CustomBotMessage.source_user_id))).where(CustomBotMessage.bot_connection_id == bot.id)) or 0)
        received = int(await db.scalar(select(func.count(CustomBotMessage.id)).where(CustomBotMessage.bot_connection_id == bot.id, CustomBotMessage.direction == "in")) or 0)
        new_today = int(await db.scalar(select(func.count(CustomBotMessage.id)).where(CustomBotMessage.bot_connection_id == bot.id, CustomBotMessage.direction == "in", CustomBotMessage.created_at >= start)) or 0)
        sent = int(await db.scalar(select(func.count(CustomBotMessage.id)).where(CustomBotMessage.bot_connection_id == bot.id, CustomBotMessage.direction == "out")) or 0)
        return (f"📊 Статистика @{bot.username.lstrip('@')}\n\n👥 Пользователей всего: {total_users}\n🆕 Новых сегодня: {new_today}\n💬 Сообщений получено: {received}\n↩️ Ответов отправлено: {sent}",
                buttons(("⬅️ Управление ботом", f"custom_bot:{bot.id}")))
    if screen.startswith("custom_bot_broadcast:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot:
            return "Бот не найден.", buttons(("⬅️ Мои боты", "my_bots"))
        return ("📣 Рассылка\n\nОтправьте сообщение, которое нужно разослать пользователям этого бота.",
                buttons(("❌ Отмена", f"custom_bot_broadcast_cancel:{bot.id}")))
    if screen.startswith("custom_bot_broadcast_confirm:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        return ("📣 Подтвердите рассылку сообщения.", buttons(("✅ Отправить", f"custom_bot_broadcast_send:{bot.id}"), ("❌ Отмена", f"custom_bot:{bot.id}"))) if bot else ("Бот не найден.", buttons(("⬅️ Мои боты", "my_bots")))
    if screen.startswith("custom_bot_disable_confirm:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot:
            return "Бот не найден.", buttons(("⬅️ Мои боты", "my_bots"))
        return (f"⛔ Отключить @{bot.username.lstrip('@')}?\n\nMAXIBOT перестанет принимать и обрабатывать сообщения этого бота.\n\nСам бот в MAX удалён не будет.",
                buttons(("⛔ Отключить", f"custom_bot_disable_confirmed:{bot.id}"), ("❌ Отмена", f"custom_bot:{bot.id}")))
    if screen.startswith("custom_bot_dialog:"):
        _, bot_id, user_id = screen.split(":", 2)
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot:
            return "Бот не найден.", buttons(("⬅️ Мои боты", "my_bots"))
        messages = list((await db.scalars(select(CustomBotMessage).where(CustomBotMessage.bot_connection_id == bot.id, CustomBotMessage.source_user_id == user_id).order_by(CustomBotMessage.created_at))).all())
        lines = [f"👤 Диалог {user_id}", ""]
        for message in messages[-10:]:
            who = "🟦 Вы" if message.direction == "out" else "👤 Пользователь"
            lines.extend([f"{who} · {_time(message.created_at)}", message.text or "Вложение", ""])
        return "\n".join(lines).rstrip(), buttons(("⬅️ Диалоги", f"custom_bot_dialogs:{bot.id}"), ("⬅️ Управление ботом", f"custom_bot:{bot.id}"))
    if screen.startswith("custom_bot_template_view:"):
        _, bot_id, kind = screen.split(":", 2)
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot or kind not in ("welcome", "sent"):
            return "Бот не найден.", buttons(("⬅️ Мои боты", "my_bots"))
        title = "✏️ Текст приветствия" if kind == "welcome" else "✏️ Автоответ"
        current = bot.welcome_text if kind == "welcome" else bot.sent_text
        explanation = (
            "Этот текст пользователь получает при первом запуске вашего бота."
            if kind == "welcome" else
            "Этот текст пользователь получает после отправки сообщения администратору."
        )
        return (
            f"{title}\n\nТекущий текст:\n\n{current}\n\n{explanation}",
            buttons(
                ("✏️ Изменить", f"custom_bot_template:{bot.id}:{kind}"),
                ("♻️ По умолчанию", f"custom_bot_template_reset:{bot.id}:{kind}"),
                ("⬅️ Назад", f"custom_bot_texts:{bot.id}"),
            ),
        )
    if screen.startswith("custom_bot_template:"):
        _, bot_id, kind = screen.split(":", 2)
        title = "приветствия" if kind == "welcome" else "автоответа"
        return f"✏️ Отправьте новый текст {title}.", buttons(("❌ Отмена", f"custom_bot_template_view:{bot_id}:{kind}"))
    if screen.startswith("custom_bot_opening:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        return (f"✅ Бот найден\n\n🤖 {bot.display_name}\n@{bot.username}\n\nТеперь откройте своего бота и нажмите «Начать».", [[{"text": "🤖 Открыть бота", "type": "link", "url": f"https://max.ru/{bot.username.lstrip('@')}"}], [{"text": "⬅️ Назад", "callback_data": f"custom_bot:{bot.id}"}]]) if bot else ("Бот не найден.", buttons(("⬅️ Назад", "my_bots")))
    return "Функция пока недоступна.", buttons(("⬅️ Назад", "home"))


async def owner_admin_stats(db: AsyncSession) -> dict[str, int]:
    demo_filter = Admin.external_id != "demo"
    clients_total = int(await db.scalar(select(func.count(Admin.id)).where(demo_filter)) or 0)
    channels_total = int(await db.scalar(
        select(func.count(Channel.id)).join(Admin).where(demo_filter)
    ) or 0)
    moscow = ZoneInfo("Europe/Moscow")
    start_moscow = datetime.now(moscow).replace(hour=0, minute=0, second=0, microsecond=0)
    end_moscow = start_moscow + timedelta(days=1)
    clients_today = int(await db.scalar(select(func.count(Admin.id)).where(
        demo_filter,
        Admin.created_at >= start_moscow.astimezone(timezone.utc),
        Admin.created_at < end_moscow.astimezone(timezone.utc),
    )) or 0)
    open_tickets = int(await db.scalar(select(func.count(SupportTicket.id)).where(
        SupportTicket.status.in_(("NEW", "IN_PROGRESS"))
    )) or 0)
    custom_bots_total = int(await db.scalar(select(func.count(BotConnection.id)).join(Admin).where(demo_filter)) or 0)
    return {
        "clients_total": clients_total,
        "clients_today": clients_today,
        "channels_total": channels_total,
        "custom_bots_total": custom_bots_total,
        "open_tickets": open_tickets,
    }


class MenuRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def get_admin(self, external_id: str) -> Admin:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if not admin:
                admin = Admin(external_id=external_id)
                db.add(admin)
                await db.commit()
            await db.refresh(admin)
            return admin

    async def custom_bot_state(self, external_id: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            return (admin.custom_bot_state, admin.pending_custom_bot_id) if admin else (None, None)

    async def set_custom_bot_state(self, external_id: str, state: str | None, bot_id: str | None = None):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if admin:
                admin.custom_bot_state = state
                admin.pending_custom_bot_id = bot_id
                await db.commit()
                return True
            return False

    async def create_custom_bot(self, external_id: str, profile: dict, encrypted_token: str) -> BotConnection | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if not admin: return None
            existing = await db.scalar(select(BotConnection).where(BotConnection.owner_admin_id == admin.id, BotConnection.max_bot_id == str(profile.get("user_id") or profile.get("id") or profile.get("bot_id"))))
            if existing:
                existing.encrypted_token = encrypted_token
                existing.status = "WAITING_ADMIN_START"
                existing.updated_at = datetime.now(timezone.utc)
                bot = existing
            else:
                bot = BotConnection(owner_admin_id=admin.id, owner_max_user_id=external_id,
                    max_bot_id=str(profile.get("user_id") or profile.get("id") or profile.get("bot_id") or "unknown"),
                    username=str(profile.get("username") or profile.get("name") or "bot"),
                    display_name=str(profile.get("name") or profile.get("display_name") or profile.get("username") or "MAX-бот"),
                    encrypted_token=encrypted_token, status="WAITING_ADMIN_START")
                db.add(bot)
            await db.commit(); await db.refresh(bot)
            return bot

    async def bot_owned(self, external_id: str, bot_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(BotConnection).join(Admin).where(BotConnection.id == bot_id, Admin.external_id == external_id))

    async def active_custom_bots(self):
        async with self.session_factory() as db:
            return list((await db.scalars(select(BotConnection).where(BotConnection.status.in_(("ACTIVE", "WAITING_ADMIN_START"))))).all())

    async def mark_custom_bot_invalid_token(self, bot_id: str) -> None:
        async with self.session_factory() as db:
            bot = await db.get(BotConnection, bot_id)
            if bot and bot.status != "INVALID_TOKEN":
                bot.status = "INVALID_TOKEN"
                await db.commit()

    async def bot_by_id(self, bot_id: str | None):
        if not bot_id: return None
        async with self.session_factory() as db:
            return await db.get(BotConnection, bot_id)

    async def custom_channels_for_bot(self, bot_id: str):
        async with self.session_factory() as db:
            return list((await db.scalars(select(Channel).where(Channel.bot_connection_id == bot_id, Channel.communication_mode == "CUSTOM_BOT").order_by(Channel.title))).all())

    async def get_channel_owned_by_bot(self, channel_id: str | None, bot_id: str):
        if not channel_id: return None
        async with self.session_factory() as db:
            return await db.scalar(select(Channel).where(Channel.id == channel_id, Channel.bot_connection_id == bot_id, Channel.communication_mode == "CUSTOM_BOT"))

    async def ensure_custom_session(self, bot_id: str, user_id: str, chat_id: str, channel_id: str):
        async with self.session_factory() as db:
            row = await db.scalar(select(CustomBotSession).where(CustomBotSession.bot_connection_id == bot_id, CustomBotSession.user_external_id == user_id))
            if not row:
                row = CustomBotSession(bot_connection_id=bot_id, user_external_id=user_id, private_chat_id=str(chat_id), current_channel_id=channel_id)
                db.add(row)
            else:
                row.private_chat_id = str(chat_id); row.current_channel_id = channel_id; row.updated_at = datetime.now(timezone.utc)
            await db.commit()

    async def custom_session(self, bot_id: str, user_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(CustomBotSession).where(CustomBotSession.bot_connection_id == bot_id, CustomBotSession.user_external_id == user_id))

    async def custom_bot_recipients(self, external_id: str, bot_id: str) -> list[str]:
        async with self.session_factory() as db:
            rows = await db.scalars(
                select(CustomBotSession.private_chat_id)
                .join(BotConnection, BotConnection.id == CustomBotSession.bot_connection_id)
                .join(Admin, Admin.id == BotConnection.owner_admin_id)
                .where(BotConnection.id == bot_id, Admin.external_id == external_id)
            )
            return [str(value) for value in rows.all() if value]

    async def set_custom_bot_status(self, external_id: str, bot_id: str, status: str) -> bool:
        async with self.session_factory() as db:
            bot = await db.scalar(select(BotConnection).join(Admin).where(BotConnection.id == bot_id, Admin.external_id == external_id))
            if not bot:
                return False
            bot.status = status
            bot.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return True

    async def activate_custom_bot(self, bot_id: str, owner_user_id: str, chat_id: str | None) -> bool:
        async with self.session_factory() as db:
            bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_max_user_id == owner_user_id))
            if not bot: return False
            bot.status = "ACTIVE"; bot.admin_private_chat_id = str(chat_id or owner_user_id); bot.updated_at = datetime.now(timezone.utc)
            await db.commit(); return True

    async def update_custom_bot_token(self, external_id: str, bot_id: str, encrypted_token: str) -> bool:
        async with self.session_factory() as db:
            bot = await db.scalar(select(BotConnection).join(Admin).where(BotConnection.id == bot_id, Admin.external_id == external_id))
            if not bot: return False
            bot.encrypted_token = encrypted_token; bot.status = "WAITING_ADMIN_START"; bot.admin_private_chat_id = None; await db.commit(); return True

    async def update_custom_bot_template(self, external_id: str, bot_id: str, kind: str, text: str) -> bool:
        async with self.session_factory() as db:
            bot = await db.scalar(select(BotConnection).join(Admin).where(BotConnection.id == bot_id, Admin.external_id == external_id))
            if not bot or kind not in ("welcome", "sent"): return False
            if kind == "welcome": bot.welcome_text = text
            else: bot.sent_text = text
            await db.commit(); return True

    async def reset_custom_bot_template(self, external_id: str, bot_id: str, kind: str) -> bool:
        async with self.session_factory() as db:
            bot = await db.scalar(select(BotConnection).join(Admin).where(BotConnection.id == bot_id, Admin.external_id == external_id))
            if not bot or kind not in ("welcome", "sent"):
                return False
            if kind == "welcome":
                bot.welcome_text = "✅ Напишите ваш вопрос — я передам его администратору."
            else:
                bot.sent_text = "✅ Сообщение отправлено.\nОжидайте ответ."
            await db.commit()
            return True

    async def toggle_custom_channel(self, external_id: str, bot_id: str, channel_id: str) -> bool:
        async with self.session_factory() as db:
            row = await db.execute(select(Channel, BotConnection).join(BotConnection, BotConnection.owner_admin_id == Channel.admin_id).join(Admin, Admin.id == Channel.admin_id).where(Channel.id == channel_id, BotConnection.id == bot_id, Admin.external_id == external_id))
            channel, bot = row.first() or (None, None)
            if not channel: return False
            channel.bot_connection_id = None if channel.bot_connection_id == bot.id else bot.id
            channel.communication_mode = "CUSTOM_BOT" if channel.bot_connection_id else "SYSTEM_INBOX"
            channel.max_connection_kind = BOT_CONNECTION if channel.bot_connection_id else PRIMARY
            await db.commit(); return True

    async def custom_channel_ids(self, external_id: str, bot_id: str):
        async with self.session_factory() as db:
            return set((await db.scalars(select(Channel.id).join(Admin).where(Admin.external_id == external_id, Channel.bot_connection_id == bot_id))).all())

    async def save_custom_bot_channels(self, external_id: str, bot_id: str):
        return True

    async def delete_custom_bot(self, external_id: str, bot_id: str) -> bool:
        async with self.session_factory() as db:
            bot = await db.scalar(select(BotConnection).join(Admin).where(BotConnection.id == bot_id, Admin.external_id == external_id))
            if not bot: return False
            await db.delete(bot); await db.commit(); return True

    async def custom_bot_for_channel(self, channel_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(BotConnection).join(Channel, Channel.bot_connection_id == BotConnection.id).where(Channel.id == channel_id, Channel.communication_mode == "CUSTOM_BOT", BotConnection.status == "ACTIVE"))

    async def active_custom_channels(self, bot_id: str):
        """Return accepting channels explicitly bound to a custom bot.

        A custom bot may not acknowledge a visitor as delivered unless the
        bot has an unambiguous channel destination.  This prevents a valid
        bot dialog from masquerading as a configured channel inbox.
        """
        async with self.session_factory() as db:
            return list((await db.scalars(select(Channel).where(
                Channel.bot_connection_id == bot_id,
                Channel.communication_mode == "CUSTOM_BOT",
                Channel.accepting.is_(True),
            ).order_by(Channel.title))).all())

    async def channel_owned(self, external_id: str, channel_id: str) -> bool:
        async with self.session_factory() as db:
            return bool(await db.scalar(select(Channel.id).join(Admin).where(Channel.id == channel_id, Admin.external_id == external_id)))

    async def connect_channel_from_bot_added(self, channel_external_id: str,
                                             initiator_user_id: str | None,
                                             chat: dict) -> dict:
        """Create or refresh an owner-scoped channel from PRIMARY bot_added."""
        payload = official_bot_added_channel(chat, initiator_user_id)
        if not channel_external_id:
            return {"status": "rejected", "reason": "missing_chat_id"}
        if not payload:
            return {"status": "rejected", "reason": "official_channel_owner_or_link_check_failed"}
        owner_id = payload["owner_user_id"]
        async with self.session_factory() as db:
            channel = await db.scalar(select(Channel).where(Channel.external_id == str(channel_external_id)).with_for_update())
            admin = await db.scalar(select(Admin).where(Admin.external_id == owner_id))
            if channel:
                current_admin = await db.get(Admin, channel.admin_id)
                current_owner = str(channel.max_owner_user_id or (current_admin.external_id if current_admin else ""))
                if current_owner and current_owner != owner_id:
                    return {"status": "rejected", "reason": "channel_owned_by_another_admin"}
                if current_admin and str(current_admin.external_id) != owner_id:
                    return {"status": "rejected", "reason": "channel_admin_conflict"}
                if not admin:
                    admin = current_admin
                if not admin:
                    return {"status": "rejected", "reason": "owner_admin_unavailable"}
                was_connected = bool(
                    channel.max_owner_user_id == owner_id
                    and channel.max_chat_metadata_json == payload["metadata"]
                    and not channel.bot_connection_id
                )
                channel.max_owner_user_id = owner_id
                channel.max_chat_metadata_json = payload["metadata"]
                if not channel.bot_connection_id:
                    channel.max_connection_kind = PRIMARY
                profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id))
                if not profile:
                    db.add(TrafficProfile(
                        channel_id=channel.id, enabled=True, target_url=payload["target_url"],
                        attribution_window_hours=settings.traffic_attribution_window_hours,
                        conversion_policy=settings.traffic_conversion_policy,
                    ))
                else:
                    profile.target_url = payload["target_url"]
                landing = await db.scalar(select(LandingPage).where(
                    LandingPage.channel_id == channel.id, LandingPage.is_active.is_(True),
                ))
                if not landing:
                    db.add(LandingPage(
                        channel_id=channel.id, public_id=new_landing_public_id(),
                        title=payload["title"], description=promotion_landing_description(None),
                        avatar_url=payload["avatar_url"],
                    ))
                await db.commit()
                return {"status": "duplicate" if was_connected and landing else "updated", "channel_id": channel.id, "title": channel.title}

            if not admin:
                admin = Admin(external_id=owner_id)
                db.add(admin)
                await db.flush()
            registry = await db.scalar(select(ChannelSlugRegistry).where(
                ChannelSlugRegistry.max_channel_external_id == str(channel_external_id)
            ))
            if registry:
                slug = registry.public_slug
                if await db.scalar(select(Channel.id).where(Channel.public_slug == slug)):
                    return {"status": "rejected", "reason": "channel_slug_conflict"}
                registry.last_connected_at = datetime.now(timezone.utc)
                registry.disconnected_at = None
            else:
                slug = self._slug(payload["title"] or str(channel_external_id))
                while await db.scalar(select(Channel.id).where(Channel.public_slug == slug)):
                    slug = self._slug(payload["title"] or str(channel_external_id))
                db.add(ChannelSlugRegistry(
                    max_channel_external_id=str(channel_external_id), public_slug=slug,
                    created_at=datetime.now(timezone.utc), last_connected_at=datetime.now(timezone.utc),
                ))
            channel = Channel(
                admin_id=admin.id, external_id=str(channel_external_id), title=payload["title"],
                avatar_url=payload["avatar_url"], public_slug=slug, max_connection_kind=PRIMARY,
                max_owner_user_id=owner_id, max_chat_metadata_json=payload["metadata"],
            )
            db.add(channel)
            await db.flush()
            db.add(TrafficProfile(
                channel_id=channel.id, enabled=True, target_url=payload["target_url"],
                attribution_window_hours=settings.traffic_attribution_window_hours,
                conversion_policy=settings.traffic_conversion_policy,
            ))
            db.add(LandingPage(
                channel_id=channel.id, public_id=new_landing_public_id(), title=payload["title"],
                description=promotion_landing_description(None), avatar_url=payload["avatar_url"],
            ))
            await db.commit()
            return {"status": "created", "channel_id": channel.id, "title": payload["title"]}

    async def resolve_owned_channel_id(self, external_id: str, reference: str) -> str | None:
        """Resolve current and legacy channel references without crossing owners."""
        if not reference:
            return None
        async with self.session_factory() as db:
            channel = await db.scalar(
                select(Channel)
                .join(Admin)
                .where(
                    Admin.external_id == str(external_id),
                    (Channel.id == reference)
                    | (Channel.external_id == str(reference))
                    | (Channel.public_slug == str(reference)),
                )
            )
            return str(channel.id) if channel else None

    async def sync_max_chat_metadata(self, channel_external_id: str, owner_user_id: str | None,
                                     chat: dict) -> dict | None:
        """Store sanitized chat metadata and the official private link.

        The method is deliberately scoped to the already connected channel;
        it never creates a channel from an untrusted bot event.
        """
        if not channel_external_id or not isinstance(chat, dict):
            return None
        link = chat.get("link")
        is_public = bool(chat.get("is_public", chat.get("public", False)))
        target = link if not is_public and valid_max_target(link) else None
        metadata = {
            "title": chat.get("title") or chat.get("name"),
            "icon": chat.get("icon") if isinstance(chat.get("icon"), str) else None,
            "is_public": is_public,
            "link_present": bool(target),
            "owner_id_present": bool(chat.get("owner_id")),
            "participants_count": chat.get("participants_count") if isinstance(chat.get("participants_count"), int) else None,
        }
        async with self.session_factory() as db:
            channel = await db.scalar(select(Channel).where(Channel.external_id == str(channel_external_id)))
            if not channel:
                return None
            chat_owner_id = chat.get("owner_id") or chat.get("owner_user_id")
            if owner_user_id:
                owner_admin = await db.scalar(select(Admin).where(Admin.external_id == str(owner_user_id)))
                # A channel created by an earlier isolated/test bootstrap may
                # have no MAX owner binding yet. Adopt it only when the
                # official chat metadata confirms that the bot_added initiator
                # is the channel owner. Existing owned channels never change
                # owner through this path.
                if owner_admin and channel.admin_id != owner_admin.id:
                    if channel.max_owner_user_id and str(channel.max_owner_user_id) != str(owner_user_id):
                        return None
                    if str(chat_owner_id or "") != str(owner_user_id):
                        return None
                    channel.admin_id = owner_admin.id
            # A bot_added event handled by the primary production client is
            # the authoritative connection signal for this channel.  Custom
            # bot bindings are never overwritten here.
            if not channel.bot_connection_id:
                channel.max_connection_kind = PRIMARY
            channel.max_owner_user_id = str(chat_owner_id or owner_user_id) if (chat_owner_id or owner_user_id) else channel.max_owner_user_id
            channel.max_chat_metadata_json = json.dumps(metadata, ensure_ascii=False)
            profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id))
            if not profile:
                profile = TrafficProfile(channel_id=channel.id, enabled=True,
                                         attribution_window_hours=settings.traffic_attribution_window_hours,
                                         conversion_policy=settings.traffic_conversion_policy)
                db.add(profile)
            if target:
                profile.target_url = target
            await db.commit()
            return {"channel_id": channel.id, "target_url": target, "is_public": is_public,
                    "link_present": bool(target), "participants_count": metadata["participants_count"]}

    async def mark_max_channel_removed(self, channel_external_id: str, owner_user_id: str | None) -> bool:
        async with self.session_factory() as db:
            query = select(Channel).where(Channel.external_id == str(channel_external_id))
            if owner_user_id:
                query = query.join(Admin).where(Admin.external_id == str(owner_user_id))
            channel = await db.scalar(query)
            if not channel:
                return False
            channel.accepting = False
            await db.commit()
            return True

    async def create_landing(self, external_id: str, channel_id: str) -> LandingPage | None:
        async with self.session_factory() as db:
            channel = await db.scalar(select(Channel).join(Admin).where(Channel.id == channel_id, Admin.external_id == external_id))
            if not channel:
                return None
            landing = await db.scalar(select(LandingPage).where(LandingPage.channel_id == channel.id, LandingPage.is_active.is_(True)))
            if landing:
                profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id))
                if not profile:
                    db.add(TrafficProfile(
                        channel_id=channel.id,
                        enabled=True,
                        target_url=None,
                        attribution_window_hours=settings.traffic_attribution_window_hours,
                        conversion_policy=settings.traffic_conversion_policy,
                    ))
                    await db.commit()
                return landing
            landing = LandingPage(
                channel_id=channel.id,
                public_id=new_landing_public_id(),
                title=channel.title,
                description=promotion_landing_description(None),
                avatar_url=channel.avatar_url,
            )
            db.add(landing)
            profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id))
            if not profile:
                profile = TrafficProfile(
                    channel_id=channel.id,
                    enabled=True,
                    target_url=None,
                    attribution_window_hours=settings.traffic_attribution_window_hours,
                    conversion_policy=settings.traffic_conversion_policy,
                )
                db.add(profile)
            await db.commit()
            await db.refresh(landing)
            return landing

    async def bind_traffic_visit(self, token: str, max_user_id: str) -> tuple[str, str] | None:
        async with self.session_factory() as db:
            visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.visit_token == token))
            if not visit:
                return None
            visit.max_user_id = str(max_user_id)
            visit.max_opened_at = datetime.now(timezone.utc)
            await db.commit()
            channel = await db.get(Channel, visit.channel_id)
            return (visit.id, channel.title) if channel else None

    async def bind_traffic_startapp(self, token: str, max_user_id: str, user_name: str | None = None) -> dict | None:
        """Bind a MAX-authenticated bot start event to an opaque visitor token."""
        if not token or not max_user_id:
            return None
        async with self.session_factory() as db:
            visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.startapp_token == token))
            if not visit:
                return None
            existing = await db.scalar(select(MaxIdentityLink).where(MaxIdentityLink.visitor_id == visit.id))
            if visit.landing_at and (datetime.now(timezone.utc) - visit.landing_at).total_seconds() > settings.traffic_startapp_ttl_seconds:
                # iOS may replay a cached bot deep-link.  Reuse it only when
                # it is already bound to this same MAX user.  If a current
                # visit for that user/channel was already bound by the first
                # delivery, route through it again instead of emitting the
                # misleading "expired" message on MAX's duplicate delivery.
                if not existing or existing.max_user_id != str(max_user_id):
                    visit.status = "EXPIRED"
                    await db.commit()
                    return None
                fresh_visits = (
                    await db.scalars(
                        select(TrafficVisit)
                        .where(
                            TrafficVisit.channel_id == visit.channel_id,
                            TrafficVisit.max_user_id.is_(None),
                            TrafficVisit.startapp_token != token,
                            TrafficVisit.landing_at >= datetime.now(timezone.utc) - timedelta(minutes=15),
                        )
                        .order_by(TrafficVisit.landing_at.desc())
                        .limit(2)
                    )
                ).all()
                if len(fresh_visits) == 1:
                    visit = fresh_visits[0]
                    existing = await db.scalar(select(MaxIdentityLink).where(MaxIdentityLink.visitor_id == visit.id))
                    logger.warning("TRAFFIC_STARTAPP_STALE_HANDOFF unique_fresh_visit=%s", True)
                elif len(fresh_visits) == 0:
                    recent_bound = await db.scalar(
                        select(TrafficVisit)
                        .where(
                            TrafficVisit.channel_id == visit.channel_id,
                            TrafficVisit.max_user_id == str(max_user_id),
                            TrafficVisit.landing_at >= datetime.now(timezone.utc) - timedelta(minutes=15),
                        )
                        .order_by(TrafficVisit.landing_at.desc())
                        .limit(1)
                    )
                    if not recent_bound:
                        visit.status = "EXPIRED"
                        await db.commit()
                        return None
                    visit = recent_bound
                    existing = await db.scalar(select(MaxIdentityLink).where(MaxIdentityLink.visitor_id == visit.id))
                    logger.warning("TRAFFIC_STARTAPP_STALE_HANDOFF recent_bound_visit=%s", True)
                else:
                    visit.status = "EXPIRED"
                    await db.commit()
                    return None
            if existing and existing.max_user_id != str(max_user_id):
                return None
            if not existing:
                import hashlib
                existing = MaxIdentityLink(
                    visitor_id=visit.id,
                    max_user_id=str(max_user_id),
                    startapp_token=token,
                    user_name=user_name,
                    init_data_fingerprint=hashlib.sha256(f"max-update:{token}:{max_user_id}".encode()).hexdigest(),
                )
                db.add(existing)
            visit.max_user_id = str(max_user_id)
            visit.max_opened_at = visit.max_opened_at or datetime.now(timezone.utc)
            visit.status = "BOT_STARTED"
            dedupe = f"poc:max_identity_linked:{visit.id}:{max_user_id}"
            if not await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
                db.add(TrafficEvent(channel_id=visit.channel_id, visit_id=visit.id, max_user_id=str(max_user_id),
                                    event_type="max_identity_linked", event_time=datetime.now(timezone.utc),
                                    dedupe_key=dedupe, metadata_json='{"source":"max_bot_update"}'))
            channel = await db.get(Channel, visit.channel_id)
            if not channel:
                return None
            profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id, TrafficProfile.enabled.is_(True)))
            target = profile.target_url if profile and valid_max_target(profile.target_url) else None
            await db.commit()
            return {"visit_id": visit.id, "visitor_id": visit.visit_token, "channel_id": channel.external_id, "channel_title": channel.title, "target_url": target, "status": visit.status}

    async def recover_recent_traffic_start(self, max_user_id: str) -> dict | None:
        """Recover only an immediate duplicate iOS handoff for one channel.

        MAX Web can deliver a valid bot start and then hand an unrelated stale
        payload to the native iOS app.  The recovery is intentionally narrow:
        same authenticated MAX user, a binding no older than five minutes,
        and exactly one channel represented by those recent bindings.
        """
        if not max_user_id:
            return None
        async with self.session_factory() as db:
            visits = (
                await db.scalars(
                    select(TrafficVisit)
                    .where(
                        TrafficVisit.max_user_id == str(max_user_id),
                        TrafficVisit.status == "BOT_STARTED",
                        TrafficVisit.max_opened_at >= datetime.now(timezone.utc) - timedelta(minutes=5),
                    )
                    .order_by(TrafficVisit.max_opened_at.desc())
                    .limit(10)
                )
            ).all()
            channel_ids = {row.channel_id for row in visits}
            if not visits or len(channel_ids) != 1:
                return None
            visit = visits[0]
            channel = await db.get(Channel, visit.channel_id)
            if not channel:
                return None
            profile = await db.scalar(
                select(TrafficProfile).where(
                    TrafficProfile.channel_id == channel.id,
                    TrafficProfile.enabled.is_(True),
                )
            )
            target = profile.target_url if profile and valid_max_target(profile.target_url) else None
            if not target:
                return None
            return {
                "visit_id": visit.id,
                "visitor_id": visit.visit_token,
                "channel_id": channel.external_id,
                "channel_title": channel.title,
                "target_url": target,
                "status": visit.status,
            }

    async def resolve_bound_traffic_token(self, token: str, max_user_id: str) -> dict | None:
        """Resolve an old token only when it already belongs to this MAX user.

        This is routing-only recovery for MAX clients that replay an old bot
        payload. It never attributes a new click or changes visit state.
        """
        if not token or not max_user_id:
            return None
        async with self.session_factory() as db:
            visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.startapp_token == token))
            if not visit:
                return None
            identity = await db.scalar(
                select(MaxIdentityLink).where(
                    MaxIdentityLink.visitor_id == visit.id,
                    MaxIdentityLink.max_user_id == str(max_user_id),
                )
            )
            if not identity or visit.max_user_id != str(max_user_id):
                return None
            channel = await db.get(Channel, visit.channel_id)
            if not channel:
                return None
            profile = await db.scalar(
                select(TrafficProfile).where(
                    TrafficProfile.channel_id == channel.id,
                    TrafficProfile.enabled.is_(True),
                )
            )
            target = profile.target_url if profile and valid_max_target(profile.target_url) else None
            if not target:
                return None
            return {
                "visit_id": visit.id,
                "visitor_id": visit.visit_token,
                "channel_id": channel.external_id,
                "channel_title": channel.title,
                "target_url": target,
                "status": "ROUTING_ONLY",
            }

    async def mark_traffic_to_channel(self, visit_id: str, max_user_id: str) -> str | None:
        async with self.session_factory() as db:
            visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.id == visit_id, TrafficVisit.max_user_id == str(max_user_id)))
            if not visit:
                return None
            visit.to_channel_at = visit.to_channel_at or datetime.now(timezone.utc)
            channel = await db.get(Channel, visit.channel_id)
            await db.commit()
            return channel.external_id if channel else None

    async def pending_traffic_channel(self, external_id: str) -> str | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            return admin.pending_traffic_channel_id if admin else None

    async def begin_traffic_target_edit(self, external_id: str, channel_id: str) -> bool:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            owned = await db.scalar(select(Channel.id).where(Channel.id == channel_id, Channel.admin_id == admin.id)) if admin else None
            if not admin or not owned:
                return False
            admin.pending_traffic_channel_id = channel_id
            await db.commit()
            return True

    async def save_traffic_target(self, external_id: str, target_url: str) -> str | None:
        from urllib.parse import urlsplit
        target_url = target_url.strip()
        parsed = urlsplit(target_url)
        if parsed.scheme != "https" or parsed.netloc != "max.ru" or not parsed.path.strip("/"):
            return None
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            channel_id = admin.pending_traffic_channel_id if admin else None
            channel = await db.scalar(select(Channel).where(Channel.id == channel_id, Channel.admin_id == admin.id)) if admin and channel_id else None
            if not admin or not channel:
                return None
            profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id))
            if not profile:
                profile = TrafficProfile(channel_id=channel.id, attribution_window_hours=settings.traffic_attribution_window_hours, conversion_policy=settings.traffic_conversion_policy)
                db.add(profile)
            profile.target_url = target_url
            admin.pending_traffic_channel_id = None
            await db.commit()
            return channel.id

    async def record_traffic_membership(self, channel_external_id: str | None, max_user_id: str | None,
                                        event_type: str, event_id: str | None = None,
                                        event_time: datetime | None = None, metadata: dict | None = None) -> bool:
        """Persist MAX signals and run the conservative v1 matcher.

        JOIN_REQUEST_CREATED is the attribution clock. USER_ADDED only
        confirms membership, which keeps manual-approval delays out of the
        matching window.
        """
        if not channel_external_id or not max_user_id:
            return False
        event_time = event_time or datetime.now(timezone.utc)
        dedupe = f"max:{event_type}:{event_id or channel_external_id + ':' + str(max_user_id) + ':' + event_time.isoformat()}"
        async with self.session_factory() as db:
            channel = await db.scalar(select(Channel).where(Channel.external_id == str(channel_external_id)))
            if not channel:
                return False
            profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id, TrafficProfile.enabled.is_(True)))
            if not profile:
                return False
            if await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
                return True
            signal = TrafficEvent(channel_id=channel.id, max_user_id=str(max_user_id), event_type=event_type,
                                  event_time=event_time, dedupe_key=dedupe,
                                  metadata_json=json.dumps(metadata or {}, ensure_ascii=False, default=str))
            db.add(signal)
            db.add(MaxJoinEvent(
                channel_id=channel.id,
                max_user_id=str(max_user_id),
                event_type=event_type,
                requested_at=event_time if event_type.lower() in {"join_request_created", "join_request"} else None,
                added_at=event_time if event_type.lower() == "user_added" else None,
                raw_event_json=json.dumps(metadata or {}, ensure_ascii=False, default=str),
                dedupe_key=dedupe,
            ))
            if event_type.lower() in {"join_request_created", "join_request", "pending_first_seen"}:
                await db.commit()
                return True
            membership = await db.scalar(select(TrafficMembership).where(TrafficMembership.channel_id == channel.id, TrafficMembership.max_user_id == str(max_user_id)))
            if not membership:
                membership = TrafficMembership(channel_id=channel.id, max_user_id=str(max_user_id), joined_at=event_time, current_member=True,
                                               source_event_id=str(event_id) if event_id else None)
                db.add(membership)
                await db.flush()
            if event_type.lower() in {"user_removed", "member_removed", "unsubscribed"}:
                membership.current_member = False
                membership.left_at = event_time
                if membership.attributed_visit_id:
                    exact_visit = await db.get(TrafficVisit, membership.attributed_visit_id)
                    subscription = await db.scalar(select(TrafficSubscription).where(
                        TrafficSubscription.visitor_id == exact_visit.id,
                        TrafficSubscription.channel_id == channel.id,
                    ))
                    if subscription:
                        subscription.status = "lost"
                        subscription.lost_at = event_time
                    exact_visit.converted_at = exact_visit.converted_at or event_time
            else:
                membership.current_member = True
                membership.joined_at = membership.joined_at or event_time
                # Manual approval produces the official user_added event but
                # not a pending event in Bot API. A verified MAX identity is
                # deterministic evidence, provided this user resolves to one
                # visit in this channel. Multiple identity links are
                # intentionally left non-exact.
                identity_visits = list((await db.scalars(
                    select(TrafficVisit.id)
                    .join(MaxIdentityLink, MaxIdentityLink.visitor_id == TrafficVisit.id)
                    .where(
                        TrafficVisit.channel_id == channel.id,
                        MaxIdentityLink.max_user_id == str(max_user_id),
                    )
                )).all())
                exact_visit_id = resolve_exact_identity_visit(identity_visits)
                if event_type.lower() == "user_added" and exact_visit_id:
                    membership.attribution_status = EXACT
                    membership.attributed_visit_id = exact_visit_id
                    membership.attribution_score = 100
                    visit = await db.get(TrafficVisit, exact_visit_id)
                    if visit:
                        visit.converted_at = visit.converted_at or event_time
                        if can_send_offline_conversion(EXACT, visit.yclid or visit.metrica_client_id):
                            exists_outbox = await db.scalar(select(OfflineConversion.id).where(
                                OfflineConversion.visit_id == visit.id,
                                OfflineConversion.membership_id == membership.id,
                                OfflineConversion.target == "YANDEX",
                            ))
                            if not exists_outbox:
                                db.add(OfflineConversion(
                                    visit_id=visit.id, membership_id=membership.id,
                                    target="YANDEX", goal="userDidSubscribe",
                                    identifier_type="yclid" if visit.yclid else "client_id",
                                    identifier_value=visit.yclid or visit.metrica_client_id,
                                    conversion_datetime=event_time,
                                    status="dry_run" if settings.yandex_conversion_dry_run else "pending",
                                ))
                    await db.commit()
                    return True
                # A verified startapp identity is deterministic evidence for
                # this visitor. Do not mix it with the old temporal-candidate
                # path used when MAX sends a bare user_added event.
                request_signal = await db.scalar(select(TrafficEvent).where(
                    TrafficEvent.channel_id == channel.id,
                    TrafficEvent.max_user_id == str(max_user_id),
                    TrafficEvent.event_type.in_(["join_request_created", "join_request", "pending_first_seen"]),
                    TrafficEvent.event_time <= event_time,
                ).order_by(TrafficEvent.event_time.desc()))
                match_time = request_signal.event_time if request_signal else event_time
                # A delayed USER_ADDED from a manually approved request is
                # only membership confirmation, not a usable attribution
                # clock.  Auto-approve channels may use the event itself.
                if not request_signal and (profile.channel_mode or "UNKNOWN").upper() not in {"AUTO", "AUTO_APPROVE", "AUTO-APPROVE"}:
                    visits = []
                else:
                    window_start = match_time - timedelta(seconds=settings.attribution_join_window_seconds)
                    visits = (await db.scalars(select(TrafficVisit).where(
                        TrafficVisit.channel_id == channel.id,
                        TrafficVisit.redirect_at.is_not(None),
                        TrafficVisit.redirect_at >= window_start,
                        TrafficVisit.redirect_at <= match_time,
                    ).order_by(TrafficVisit.redirect_at.desc()))).all()
                candidates = []
                for visit in visits:
                    if not visit.redirect_at:
                        continue
                    open_score = 0.0
                    if settings.max_open_signal_enabled:
                        open_types = []
                        if settings.max_open_confirmed_enabled:
                            open_types.append("MAX_OPEN_CONFIRMED")
                        if settings.max_open_likely_enabled:
                            open_types.append("MAX_OPEN_LIKELY")
                        open_event = await db.scalar(select(TrafficEvent).where(
                            TrafficEvent.visit_id == visit.id,
                            TrafficEvent.event_type.in_(open_types or ["__disabled__"]),
                            TrafficEvent.event_time <= match_time,
                        ).order_by(TrafficEvent.event_time.asc()))
                        if open_event:
                            try:
                                open_score = float((json.loads(open_event.metadata_json or "{}").get("signal_score") or (1.0 if open_event.event_type == "MAX_OPEN_CONFIRMED" else 0.0)))
                            except (TypeError, ValueError, json.JSONDecodeError):
                                open_score = 1.0 if open_event.event_type == "MAX_OPEN_CONFIRMED" else 0.0
                    candidates.append((visit.id, (match_time - visit.redirect_at).total_seconds(), open_score))
                for visit in visits:
                    existing = await db.scalar(select(TrafficAttributionCandidate).where(TrafficAttributionCandidate.membership_id == membership.id, TrafficAttributionCandidate.visit_id == visit.id))
                    if not existing:
                        delta = (match_time - visit.redirect_at).total_seconds() if visit.redirect_at else 0
                        score = max(1, 100 - int(delta / settings.attribution_join_window_seconds * 100))
                        open_signal = next((item[2] for item in candidates if item[0] == visit.id), 0.0)
                        score += int(max(0.0, min(open_signal, 1.0)) * 25)
                        reason = "redirect_plus_max_open_likely" if open_signal else "redirect_to_join_request"
                        db.add(TrafficAttributionCandidate(membership_id=membership.id, visit_id=visit.id, score=score, reason=reason))
                decision = decide_scored(candidates, settings.attribution_join_window_seconds)
                membership.attribution_status = decision.status
                membership.attributed_visit_id = decision.visit_id
                membership.attribution_score = decision.score
                if decision.status in {EXACT, HIGH} and decision.visit_id:
                    visit = await db.get(TrafficVisit, decision.visit_id)
                    if visit:
                        visit.converted_at = visit.converted_at or event_time
                        if can_send_offline_conversion(decision.status, visit.yclid or visit.metrica_client_id):
                            exists_outbox = await db.scalar(select(OfflineConversion.id).where(
                                OfflineConversion.visit_id == visit.id,
                                OfflineConversion.membership_id == membership.id,
                                OfflineConversion.target == "YANDEX",
                            ))
                            if not exists_outbox:
                                db.add(OfflineConversion(
                                    visit_id=visit.id, membership_id=membership.id,
                                    identifier_type="yclid" if visit.yclid else "client_id",
                                    identifier_value=visit.yclid or visit.metrica_client_id,
                                    conversion_datetime=event_time,
                                    status="dry_run" if settings.yandex_conversion_dry_run else "pending",
                                ))
            await db.commit()
            return True

    async def is_admin(self, external_id: str | None) -> bool:
        if not external_id:
            return False
        async with self.session_factory() as db:
            return bool(await db.scalar(select(Admin.id).where(Admin.external_id == external_id)))

    async def ensure_exact_user_added_attribution(self, channel_external_id: str,
                                                  max_user_id: str,
                                                  event_time: datetime | None = None) -> bool:
        """Promote an already-recorded official member event by exact identity.

        This is intentionally idempotent and channel-scoped. It is useful for
        replay-safe reconciliation when the membership event was persisted
        before the exact-identity rule was deployed.
        """
        if not channel_external_id or not max_user_id:
            return False
        event_time = event_time or datetime.now(timezone.utc)
        async with self.session_factory() as db:
            channel = await db.scalar(select(Channel).where(Channel.external_id == str(channel_external_id)))
            if not channel:
                return False
            membership = await db.scalar(select(TrafficMembership).where(
                TrafficMembership.channel_id == channel.id,
                TrafficMembership.max_user_id == str(max_user_id),
            ))
            identity_visits = list((await db.scalars(
                select(TrafficVisit.id)
                .join(MaxIdentityLink, MaxIdentityLink.visitor_id == TrafficVisit.id)
                .where(TrafficVisit.channel_id == channel.id, MaxIdentityLink.max_user_id == str(max_user_id))
            )).all())
            exact_visit_id = resolve_exact_identity_visit(identity_visits)
            if not membership or not exact_visit_id:
                return False
            visit = await db.get(TrafficVisit, exact_visit_id)
            if not visit or not (visit.yclid or visit.metrica_client_id):
                return False
            membership.attribution_status = EXACT
            membership.attributed_visit_id = exact_visit_id
            membership.attribution_score = 100
            visit.converted_at = visit.converted_at or event_time
            exists_outbox = await db.scalar(select(OfflineConversion.id).where(
                OfflineConversion.visit_id == visit.id,
                OfflineConversion.membership_id == membership.id,
                OfflineConversion.target == "YANDEX",
            ))
            if not exists_outbox:
                db.add(OfflineConversion(
                    visit_id=visit.id, membership_id=membership.id,
                    target="YANDEX", goal="userDidSubscribe",
                    identifier_type="yclid" if visit.yclid else "client_id",
                    identifier_value=visit.yclid or visit.metrica_client_id,
                    conversion_datetime=event_time,
                    status="dry_run" if settings.yandex_conversion_dry_run else "pending",
                ))
            await db.commit()
            return True

    async def render(self, external_id: str, screen: str, channel_id: str | None = None):
        # Use the existing idempotent repository operation before opening the
        # read transaction used for rendering. It also enforces ownership.
        if screen.startswith("promotion_landing:"):
            selected_id = screen.split(":", 1)[1]
            await self.create_landing(external_id, selected_id)
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if not admin:
                admin = Admin(external_id=external_id)
                db.add(admin)
                await db.flush()
            text, keyboard = await render_screen(db, admin, screen, channel_id)
            # Banner delivery is temporarily disabled; keep the render flow
            # text-only on every screen until a final MAX media layout is
            # approved.
            media = []
            return text, keyboard, admin.service_message_id, media

    async def save_service_message(self, external_id: str, message_id: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if not admin:
                admin = Admin(external_id=external_id)
                db.add(admin)
            admin.service_message_id = message_id
            await db.commit()

    async def service_message_id(self, external_id: str) -> str | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            return admin.service_message_id if admin else None

    async def messages_ui_ids(self, external_id: str) -> tuple[str | None, str | None]:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            return (
                admin.messages_header_message_id if admin else None,
                admin.messages_controls_message_id if admin else None,
            )

    async def save_messages_header(self, external_id: str, message_id: str | None) -> None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if admin:
                admin.messages_header_message_id = message_id
                await db.commit()

    async def save_messages_controls(self, external_id: str, message_id: str | None) -> None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if admin:
                admin.messages_controls_message_id = message_id
                await db.commit()

    async def clear_messages_ui(self, external_id: str) -> None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if admin:
                admin.messages_header_message_id = None
                admin.messages_controls_message_id = None
                await db.commit()

    async def begin_feed_session(self, external_id: str, channel_id: str) -> tuple[str | None, str]:
        """Set the active native feed and return (old_session, new_session)."""
        session_id = __import__("uuid").uuid4().hex
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            old = admin.feed_session_id if admin else None
            if admin:
                admin.active_feed_channel_id = channel_id
                admin.feed_session_id = session_id
                await db.commit()
            return old, session_id

    async def end_feed_session(self, external_id: str) -> str | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            old = admin.feed_session_id if admin else None
            if admin:
                admin.active_feed_channel_id = None
                admin.feed_session_id = None
                await db.commit()
            return old

    async def active_feed(self, external_id: str) -> tuple[str | None, str | None]:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            return (admin.active_feed_channel_id, admin.feed_session_id) if admin else (None, None)

    async def set_global_feed_open(self, external_id: str) -> None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if admin:
                admin.active_feed_channel_id = "__global__"
                admin.feed_session_id = None
                await db.commit()

    async def set_self_test(self, external_id: str, channel_id: str) -> None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if admin:
                admin.self_test_mode = True
                admin.self_test_channel_id = channel_id
                await db.commit()

    async def self_test_channel(self, external_id: str) -> str | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            return admin.self_test_channel_id if admin and admin.self_test_mode else None

    async def is_self_test_message(self, message_id: str) -> bool:
        async with self.session_factory() as db:
            return bool(await db.scalar(select(Message.is_self_test).where(Message.id == message_id)))

    async def feed_rows(self, admin_external_id: str, channel_id: str, limit: int = 20) -> list[Message]:
        async with self.session_factory() as db:
            admin_id = await db.scalar(select(Admin.id).where(Admin.external_id == admin_external_id))
            rows = (await db.scalars(
                select(Message).join(Conversation).join(Channel)
                .options(selectinload(Message.conversation))
                .where(Channel.id == channel_id, Channel.admin_id == admin_id)
                .order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
            )).all()
            return list(reversed(rows))

    async def global_feed_rows(self, admin_external_id: str, limit: int | None = None) -> list[Message]:
        async with self.session_factory() as db:
            admin_id = await db.scalar(select(Admin.id).where(Admin.external_id == admin_external_id))
            query = (select(Message).join(Conversation).join(Channel)
                .options(selectinload(Message.conversation).selectinload(Conversation.channel))
                .where(
                    Channel.admin_id == admin_id,
                    Message.direction == "in",
                    Message.admin_feed_delivered.is_(False),
                    ~exists().where(AdminFeedMessage.source_message_id == Message.id),
                )
                .order_by(Message.created_at.desc(), Message.id.desc()))
            if limit is not None:
                query = query.limit(limit)
            rows = (await db.scalars(query)).all()
            return list(reversed(rows))

    async def mark_channel_read(self, admin_external_id: str, channel_id: str) -> None:
        async with self.session_factory() as db:
            admin_id = await db.scalar(select(Admin.id).where(Admin.external_id == admin_external_id))
            conversations = (await db.scalars(
                select(Conversation).join(Channel)
                .where(Channel.id == channel_id, Channel.admin_id == admin_id)
            )).all()
            for conversation in conversations:
                conversation.unread = 0
            await db.commit()

    async def mark_all_read(self, admin_external_id: str) -> None:
        async with self.session_factory() as db:
            admin_id = await db.scalar(select(Admin.id).where(Admin.external_id == admin_external_id))
            conversations = (await db.scalars(
                select(Conversation).join(Channel)
                .where(Channel.admin_id == admin_id)
            )).all()
            for conversation in conversations:
                conversation.unread = 0
            await db.commit()

    async def save_feed_mapping(self, admin_feed_message_id: str, source_message_id: str, source_external_message_id: str | None, source_user_id: str, channel_id: str | None, feed_session_id: str, source_private_chat_id: str | None = None, source_bot_connection_id: str | None = None, destination_admin_chat_id: str | None = None) -> None:
        async with self.session_factory() as db:
            exists = await db.scalar(select(AdminFeedMessage.id).where(AdminFeedMessage.admin_feed_message_id == admin_feed_message_id))
            if not exists:
                db.add(AdminFeedMessage(
                    admin_feed_message_id=admin_feed_message_id,
                    source_message_id=source_message_id,
                    source_external_message_id=source_external_message_id,
                    source_user_id=source_user_id,
                    channel_id=channel_id,
                    feed_session_id=feed_session_id,
                    source_private_chat_id=source_private_chat_id,
                    source_bot_connection_id=source_bot_connection_id,
                    source_transport="CUSTOM_BOT" if source_bot_connection_id else "SYSTEM_INBOX",
                    destination_admin_chat_id=destination_admin_chat_id,
                ))
                source = await db.get(Message, source_message_id)
                if source:
                    source.admin_feed_delivered = True
                await db.commit()

    async def create_custom_inbound(self, bot_id: str, source_user_id: str, source_private_chat_id: str,
                                    external_message_id: str | None, text: str, attachments: list[dict]) -> tuple[str, bool]:
        """Persist one standalone-bot inbound message, idempotently."""
        async with self.session_factory() as db:
            existing = None
            if external_message_id:
                existing = await db.scalar(select(CustomBotMessage).where(CustomBotMessage.external_message_id == external_message_id))
            if existing:
                return existing.id, False
            row = CustomBotMessage(
                bot_connection_id=bot_id, direction="in", source_user_id=str(source_user_id),
                source_private_chat_id=str(source_private_chat_id), external_message_id=external_message_id,
                text=text or "", attachments_json=json.dumps(attachments or [], ensure_ascii=False),
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id, True

    async def save_custom_outbound(self, bot_id: str, mapping: AdminFeedMessage,
                                   external_message_id: str | None, text: str,
                                   attachments: list[dict]) -> str | None:
        async with self.session_factory() as db:
            row = CustomBotMessage(
                bot_connection_id=bot_id, direction="out", source_user_id=str(mapping.source_user_id),
                source_private_chat_id=str(mapping.source_private_chat_id or mapping.source_user_id),
                external_message_id=external_message_id, text=text or "",
                attachments_json=json.dumps(attachments or [], ensure_ascii=False),
                reply_to_message_id=mapping.source_message_id,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    async def feed_message_ids(self, feed_session_id: str | None) -> list[str]:
        if not feed_session_id:
            return []
        async with self.session_factory() as db:
            return list((await db.scalars(select(AdminFeedMessage.admin_feed_message_id).where(AdminFeedMessage.feed_session_id == feed_session_id))).all())

    async def feed_mapping_id(self, source_message_id: str) -> str | None:
        async with self.session_factory() as db:
            return await db.scalar(select(AdminFeedMessage.admin_feed_message_id).where(AdminFeedMessage.source_message_id == source_message_id))

    async def delete_feed_mapping(self, admin_feed_message_id: str) -> None:
        async with self.session_factory() as db:
            row = await db.scalar(select(AdminFeedMessage).where(AdminFeedMessage.admin_feed_message_id == admin_feed_message_id))
            if row:
                await db.delete(row)
                await db.commit()

    async def mapping_for_reply(self, admin_external_id: str, admin_feed_message_id: str):
        async with self.session_factory() as db:
            row = await db.scalar(
                select(AdminFeedMessage).join(Channel, Channel.id == AdminFeedMessage.channel_id)
                .join(Admin, Admin.id == Channel.admin_id)
                .where(Admin.external_id == admin_external_id, AdminFeedMessage.admin_feed_message_id == admin_feed_message_id)
            )
            return row

    async def custom_mapping_for_reply(self, bot_id: str, admin_chat_id: str, admin_feed_message_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(AdminFeedMessage).where(
                AdminFeedMessage.source_bot_connection_id == bot_id,
                AdminFeedMessage.destination_admin_chat_id == str(admin_chat_id),
                AdminFeedMessage.admin_feed_message_id == str(admin_feed_message_id),
                AdminFeedMessage.source_transport == "CUSTOM_BOT",
            ))

    async def inbox_mapping_for_reply(self, inbox_chat_id: str, inbox_message_id: str):
        """Resolve a native Reply in the channel's physical inbox chat.

        Inbox feed messages reuse AdminFeedMessage as the durable mapping
        table, with feed_session_id set to ``inbox:<chat_id>``.  Restricting
        the lookup by both values prevents a reply from another inbox from
        being routed to the wrong channel.
        """
        async with self.session_factory() as db:
            return await db.scalar(
                select(AdminFeedMessage)
                .join(Channel, Channel.id == AdminFeedMessage.channel_id)
                .where(
                    Channel.inbox_chat_id == str(inbox_chat_id),
                    AdminFeedMessage.admin_feed_message_id == str(inbox_message_id),
                    AdminFeedMessage.feed_session_id == f"inbox:{inbox_chat_id}",
                )
            )

    async def save_native_reply(self, admin_external_id: str, mapping: AdminFeedMessage, text: str, attachments: list[dict] | None = None):
        async with self.session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(
                Conversation.channel_id == mapping.channel_id,
                Conversation.user_external_id == mapping.source_user_id,
            ))
            if not conversation:
                return None
            message = Message(
                conversation_id=conversation.id,
                direction="out",
                text=text,
                attachments_json=json.dumps(attachments or [], ensure_ascii=False),
                reply_to_message_id=mapping.source_message_id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(message)
            conversation.last_message_at = datetime.now(timezone.utc)
            await db.commit()
            return conversation.user_external_id, conversation.id, mapping.channel_id

    async def save_inbox_reply(self, mapping: AdminFeedMessage, text: str, attachments: list[dict] | None = None):
        """Persist an administrator reply made from a channel inbox chat."""
        async with self.session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(
                Conversation.channel_id == mapping.channel_id,
                Conversation.user_external_id == mapping.source_user_id,
            ))
            if not conversation:
                return None
            message = Message(
                conversation_id=conversation.id,
                direction="out",
                text=text,
                attachments_json=json.dumps(attachments or [], ensure_ascii=False),
                reply_to_message_id=mapping.source_message_id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(message)
            conversation.last_message_at = datetime.now(timezone.utc)
            await db.commit()
            return conversation.user_external_id, conversation.id, mapping.channel_id

    async def reset_navigation(self, external_id: str, preserve_service_message: bool = False):
        """Forget navigation state, optionally keeping the service panel id."""
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if admin:
                if not preserve_service_message:
                    admin.service_message_id = None
                admin.pending_reply_conversation_id = None
                admin.pending_template_channel_id = None
                admin.pending_template_type = None
                admin.active_feed_channel_id = None
                admin.feed_session_id = None
                admin.self_test_mode = False
                admin.self_test_channel_id = None
                admin.pending_inbox_channel_id = None
                admin.pending_traffic_channel_id = None
                admin.custom_bot_state = None
                admin.pending_custom_bot_id = None
                await db.commit()

    async def has_conversation_user(self, external_id: str) -> bool:
        async with self.session_factory() as db:
            conversation_id = await db.scalar(
                select(Conversation.id).where(Conversation.user_external_id == external_id).limit(1)
            )
            return conversation_id is not None

    async def channel_conversation_user(self, channel_id: str, candidates: list[str]) -> str | None:
        if not candidates:
            return None
        async with self.session_factory() as db:
            conversation = await db.scalar(
                select(Conversation)
                .where(Conversation.channel_id == channel_id, Conversation.user_external_id.in_(candidates))
                .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
                .limit(1)
            )
            return conversation.user_external_id if conversation else None

    async def set_receiving(self, channel_id: str, accepting: bool):
        async with self.session_factory() as db:
            channel = await db.get(Channel, channel_id)
            if channel:
                channel.accepting = accepting
                await db.commit()

    async def begin_inbox_connect(self, admin_external_id: str, channel_id: str) -> bool:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            channel = await db.scalar(select(Channel).where(Channel.id == channel_id, Channel.admin_id == admin.id)) if admin else None
            if not admin or not channel:
                return False
            admin.pending_inbox_channel_id = channel_id
            await db.commit()
            return True

    async def cancel_inbox_connect(self, admin_external_id: str) -> str | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            channel_id = admin.pending_inbox_channel_id if admin else None
            if admin:
                admin.pending_inbox_channel_id = None
                await db.commit()
            return channel_id

    async def channel_delete_snapshot(self, admin_external_id: str, channel_id: str):
        async with self.session_factory() as db:
            row = await db.execute(
                select(Channel, Admin.id)
                .join(Admin, Channel.admin_id == Admin.id)
                .where(Channel.id == channel_id, Admin.external_id == admin_external_id)
            )
            channel, admin_id = row.first() or (None, None)
            if not channel:
                return None
            return {
                "id": channel.id,
                "title": channel.title,
                "external_id": channel.external_id,
                "inbox_chat_id": channel.inbox_chat_id,
                "admin_id": admin_id,
            }

    async def delete_channel_owned(self, admin_external_id: str, channel_id: str) -> dict | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            channel = await db.scalar(
                select(Channel).where(Channel.id == channel_id, Channel.admin_id == admin.id if admin else False)
            )
            if not admin or not channel:
                return None
            registry = await db.scalar(select(ChannelSlugRegistry).where(
                ChannelSlugRegistry.max_channel_external_id == channel.external_id
            ))
            if not registry:
                registry = ChannelSlugRegistry(
                    max_channel_external_id=channel.external_id,
                    public_slug=channel.public_slug,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(registry)
            registry.last_connected_at = datetime.now(timezone.utc)
            registry.disconnected_at = datetime.now(timezone.utc)
            snapshot = {
                "id": channel.id,
                "title": channel.title,
                "external_id": channel.external_id,
                "inbox_chat_id": channel.inbox_chat_id,
                "admin_id": admin.id,
            }
            await db.execute(delete(AdminFeedMessage).where(AdminFeedMessage.channel_id == channel.id))
            if admin.pending_inbox_channel_id == channel.id:
                admin.pending_inbox_channel_id = None
            if admin.pending_template_channel_id == channel.id:
                admin.pending_template_channel_id = None
                admin.pending_template_type = None
            if admin.active_feed_channel_id == channel.id:
                admin.active_feed_channel_id = None
                admin.feed_session_id = None
            if admin.self_test_channel_id == channel.id:
                admin.self_test_channel_id = None
                admin.self_test_mode = False
            await db.delete(channel)
            await db.commit()
            return snapshot

    async def pending_inbox_channel(self, admin_external_id: str) -> str | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            return admin.pending_inbox_channel_id if admin else None

    async def pending_inbox_candidates(self) -> list[tuple[str, str, str]]:
        async with self.session_factory() as db:
            rows = (await db.execute(
                select(Admin.external_id, Channel.id, Channel.title)
                .join(Channel, Channel.id == Admin.pending_inbox_channel_id)
            )).all()
            return [(str(channel_id), str(title), str(admin_id)) for admin_id, channel_id, title in rows]

    async def pending_inbox_admins(self, channel_id: str) -> list[str]:
        async with self.session_factory() as db:
            return list((await db.scalars(select(Admin.external_id).where(Admin.pending_inbox_channel_id == channel_id))).all())

    async def bind_inbox_chat(self, admin_external_id: str, channel_id: str, chat_id: str, title: str) -> bool:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            channel = await db.scalar(select(Channel).where(Channel.id == channel_id, Channel.admin_id == admin.id)) if admin else None
            if not admin or not channel:
                return False
            channel.inbox_chat_id = str(chat_id)
            channel.inbox_chat_title = title or f"Обращения — {channel.title}"
            channel.inbox_chat_connected_at = datetime.now(timezone.utc)
            admin.pending_inbox_channel_id = None
            await db.commit()
            return True

    async def unbind_inbox_chat(self, chat_id: str) -> dict | None:
        """Clear one saved inbox binding after an official ``bot_removed``."""
        async with self.session_factory() as db:
            row = await db.execute(
                select(Channel, Admin.external_id)
                .join(Admin, Channel.admin_id == Admin.id)
                .where(Channel.inbox_chat_id == str(chat_id))
                .with_for_update()
            )
            channel, admin_external_id = row.first() or (None, None)
            if not channel:
                return None
            title = channel.inbox_chat_title or channel.title
            channel.inbox_chat_id = None
            channel.inbox_chat_title = None
            channel.inbox_chat_connected_at = None
            channel.accepting = False
            await db.commit()
            return {"admin_external_id": str(admin_external_id), "title": title}

    async def channel_by_inbox_chat(self, chat_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(Channel).where(Channel.inbox_chat_id == str(chat_id)))

    async def channel_by_inbox_chat_for_admin(self, admin_external_id: str, chat_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(Channel).join(Admin).where(Channel.inbox_chat_id == str(chat_id), Admin.external_id == admin_external_id))

    async def channel_inbox_rows(self, channel_id: str) -> list[Message]:
        async with self.session_factory() as db:
            return list((await db.scalars(
                select(Message).join(Conversation).where(Conversation.channel_id == channel_id).order_by(Message.created_at.asc(), Message.id.asc())
            )).all())

    async def create_channel(self, admin_external_id: str, data: dict):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            if not admin:
                admin = Admin(external_id=admin_external_id)
                db.add(admin)
                await db.flush()
            external_id = str(data["external_id"])
            channel = await db.scalar(select(Channel).where(Channel.admin_id == admin.id, Channel.external_id == external_id))
            if channel:
                registry = await db.scalar(select(ChannelSlugRegistry).where(
                    ChannelSlugRegistry.max_channel_external_id == external_id
                ))
                if not registry:
                    db.add(ChannelSlugRegistry(
                        max_channel_external_id=external_id,
                        public_slug=channel.public_slug,
                        created_at=datetime.now(timezone.utc),
                    ))
                    await db.commit()
                return channel.id
            registry = await db.scalar(select(ChannelSlugRegistry).where(
                ChannelSlugRegistry.max_channel_external_id == external_id
            ))
            if registry:
                slug = registry.public_slug
                slug_owner = await db.scalar(select(Channel.id).where(Channel.public_slug == slug))
                if slug_owner:
                    raise ValueError("MAX channel slug is already connected")
                registry.last_connected_at = datetime.now(timezone.utc)
                registry.disconnected_at = None
            else:
                slug = data.get("public_slug") or self._slug(data.get("title") or external_id)
                base_slug = slug
                suffix = 2
                while await db.scalar(select(Channel.id).where(Channel.public_slug == slug)):
                    slug = f"{base_slug}-{suffix}"
                    suffix += 1
                registry = ChannelSlugRegistry(
                    max_channel_external_id=external_id,
                    public_slug=slug,
                    created_at=datetime.now(timezone.utc),
                    last_connected_at=datetime.now(timezone.utc),
                )
                db.add(registry)
            chat = data.get("chat") if isinstance(data.get("chat"), dict) else {}
            link = chat.get("link")
            is_public = bool(chat.get("is_public", chat.get("public", False)))
            target = link if not is_public and valid_max_target(link) else None
            metadata = json.dumps({
                "title": chat.get("title") or data.get("title"),
                "icon_present": bool(chat.get("icon")),
                "is_public": is_public,
                "link_present": bool(target),
                "owner_id_present": bool(chat.get("owner_id")),
                "participants_count": chat.get("participants_count") if isinstance(chat.get("participants_count"), int) else None,
            }, ensure_ascii=False)
            channel = Channel(admin_id=admin.id, external_id=external_id, title=data.get("title") or "Канал MAX",
                              avatar_url=data.get("avatar_url"), public_slug=slug,
                              max_owner_user_id=admin_external_id, max_chat_metadata_json=metadata)
            db.add(channel)
            db.add(TrafficProfile(channel_id=channel.id, enabled=True, target_url=target,
                                  attribution_window_hours=settings.traffic_attribution_window_hours,
                                  conversion_policy=settings.traffic_conversion_policy))
            await db.commit()
            return channel.id

    @staticmethod
    def _slug(value: str) -> str:
        import secrets
        # Opaque, URL-safe deep-link payload similar to native MAX bots.
        return secrets.token_urlsafe(9)

    async def begin_template_edit(self, admin_external_id: str, channel_id: str, template_type: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            channel = await db.get(Channel, channel_id)
            if not admin or not channel or template_type not in ("welcome", "sent"):
                return None
            admin.pending_template_channel_id = channel_id
            admin.pending_template_type = template_type
            await db.commit()
            return channel.welcome_text if template_type == "welcome" else channel.sent_text

    async def pending_template(self, admin_external_id: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            if not admin or not admin.pending_template_channel_id or not admin.pending_template_type:
                return None
            return admin.pending_template_channel_id, admin.pending_template_type

    async def set_support_input(self, admin_external_id: str, ticket_id: str | None = None) -> bool:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            if not admin:
                return False
            admin.support_input_mode = True
            admin.support_input_ticket_id = ticket_id
            await db.commit()
            return True

    async def clear_support_input(self, admin_external_id: str) -> None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            if admin:
                admin.support_input_mode = False
                admin.support_input_ticket_id = None
                await db.commit()

    async def support_input(self, admin_external_id: str) -> tuple[bool, str | None]:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            return (bool(admin and admin.support_input_mode), admin.support_input_ticket_id if admin else None)

    async def active_support_ticket(self, admin_external_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(SupportTicket).where(
                SupportTicket.max_user_id == admin_external_id,
                SupportTicket.status.in_(("NEW", "IN_PROGRESS")),
            ).order_by(SupportTicket.created_at.desc()))

    async def create_support_ticket(self, admin_external_id: str, name: str, text: str, attachments: list[dict], support_chat_id: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            if not admin:
                return None
            ticket = SupportTicket(
                max_user_id=admin_external_id,
                source_private_chat_id=admin_external_id,
                support_chat_id=str(support_chat_id),
                source_user_name=name or "Пользователь",
                source_text=text or None,
                attachments_json=json.dumps(attachments or [], ensure_ascii=False),
            )
            db.add(ticket)
            await db.flush()
            await db.commit()
            await db.refresh(ticket)
            return ticket

    async def set_support_ticket_message(self, ticket_id: str, message_id: str, source_message_id: str | None = None) -> bool:
        async with self.session_factory() as db:
            ticket = await db.get(SupportTicket, ticket_id)
            if not ticket:
                return False
            ticket.support_chat_message_id = str(message_id)
            db.add(SupportTicketMessage(
                ticket_id=ticket.id,
                source_message_id=source_message_id,
                support_chat_message_id=str(message_id),
                source_user_id=ticket.max_user_id,
                direction="in",
            ))
            await db.commit()
            return True

    async def mark_support_delivery_failed(self, ticket_id: str) -> None:
        async with self.session_factory() as db:
            ticket = await db.get(SupportTicket, ticket_id)
            if ticket:
                ticket.status = "FAILED"
                await db.commit()

    async def support_ticket_by_message(self, support_message_id: str):
        async with self.session_factory() as db:
            row = await db.scalar(select(SupportTicketMessage).where(SupportTicketMessage.support_chat_message_id == str(support_message_id)))
            if not row:
                return None
            return await db.get(SupportTicket, row.ticket_id)

    async def support_ticket_by_id(self, ticket_id: str):
        async with self.session_factory() as db:
            return await db.get(SupportTicket, ticket_id)

    async def add_support_message_mapping(self, ticket_id: str, support_message_id: str, source_user_id: str, direction: str = "out", source_message_id: str | None = None) -> None:
        async with self.session_factory() as db:
            db.add(SupportTicketMessage(
                ticket_id=ticket_id,
                source_message_id=source_message_id,
                support_chat_message_id=str(support_message_id),
                source_user_id=str(source_user_id),
                direction=direction,
            ))
            await db.commit()

    async def support_ticket_status(self, ticket_id: str, status: str):
        async with self.session_factory() as db:
            ticket = await db.get(SupportTicket, ticket_id)
            if not ticket:
                return None
            ticket.status = status
            if status == "IN_PROGRESS" and not ticket.taken_at:
                ticket.taken_at = datetime.now(timezone.utc)
            if status == "CLOSED":
                ticket.closed_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(ticket)
            return ticket

    async def save_template(self, admin_external_id: str, text: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            if not admin or not admin.pending_template_channel_id or not admin.pending_template_type:
                return False
            channel = await db.get(Channel, admin.pending_template_channel_id)
            if not channel:
                return False
            if admin.pending_template_type == "welcome": channel.welcome_text = editable_welcome(text)
            else: channel.sent_text = text
            admin.pending_template_channel_id = None
            admin.pending_template_type = None
            await db.commit()
            return True

    async def cancel_template(self, admin_external_id: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            if admin:
                channel_id = admin.pending_template_channel_id
                admin.pending_template_channel_id = None
                admin.pending_template_type = None
                await db.commit()
                return channel_id
        return None

    async def begin_reply(self, admin_external_id: str, conversation_id: str) -> bool:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            conversation = await db.scalar(select(Conversation).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == admin.id)) if admin else None
            if not admin or not conversation:
                return False
            admin.pending_reply_conversation_id = conversation_id
            await db.commit()
            return True

    async def pending_reply(self, admin_external_id: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            return admin.pending_reply_conversation_id if admin else None

    async def clear_reply(self, admin_external_id: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            if admin:
                conversation_id = admin.pending_reply_conversation_id
                admin.pending_reply_conversation_id = None
                await db.commit()
                return conversation_id
        return None

    async def save_reply(self, admin_external_id: str, text: str):
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == admin_external_id))
            conversation_id = admin.pending_reply_conversation_id if admin else None
            conversation = await db.scalar(select(Conversation).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == admin.id)) if admin and conversation_id else None
            if not conversation:
                return None
            message = Message(conversation_id=conversation.id, direction="out", text=text)
            db.add(message)
            conversation.last_message_at = message.created_at
            admin.pending_reply_conversation_id = None
            await db.commit()
            return conversation.id, conversation.user_external_id, conversation.channel_id

    async def reset_template(self, admin_external_id: str, channel_id: str, template_type: str):
        async with self.session_factory() as db:
            channel = await db.get(Channel, channel_id)
            if not channel or template_type not in ("welcome", "sent"): return False
            if template_type == "welcome": channel.welcome_text = WELCOME
            else: channel.sent_text = SENT
            await db.commit(); return True

    async def set_blocked(self, admin_external_id: str, conversation_id: str, blocked: bool):
        async with self.session_factory() as db:
            conversation = await db.scalar(select(Conversation).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == (await db.scalar(select(Admin.id).where(Admin.external_id == admin_external_id)))))
            if not conversation:
                return False
            row = await db.scalar(select(BlockedUser).where(BlockedUser.channel_id == conversation.channel_id, BlockedUser.user_external_id == conversation.user_external_id))
            if blocked and not row:
                db.add(BlockedUser(channel_id=conversation.channel_id, user_external_id=conversation.user_external_id))
            elif not blocked and row:
                await db.delete(row)
            await db.commit()
            return True

    async def pending_feed_messages(self, admin_external_id: str, channel_id: str):
        async with self.session_factory() as db:
            rows = (await db.scalars(
                select(Message)
                .join(Conversation)
                .join(Channel)
                .options(selectinload(Message.conversation))
                .where(
                    Channel.id == channel_id,
                    Channel.admin_id == (await db.scalar(select(Admin.id).where(Admin.external_id == admin_external_id))),
                    Message.direction == "in",
                    Message.admin_feed_delivered.is_(False),
                )
                .order_by(Message.created_at.asc(), Message.id.asc())
            )).all()
            return list(rows)

    async def incoming_feed_page(self, admin_external_id: str, channel_id: str, offset: int, limit: int = 20):
        async with self.session_factory() as db:
            admin_id = await db.scalar(select(Admin.id).where(Admin.external_id == admin_external_id))
            query = (select(Message).join(Conversation).join(Channel)
                     .options(selectinload(Message.conversation))
                     .where(Channel.id == channel_id, Channel.admin_id == admin_id, Message.direction == "in")
                     .order_by(Message.created_at.desc(), Message.id.desc()))
            all_messages = list((await db.scalars(query)).all())
            start = max(0, min(offset, len(all_messages)))
            selected = all_messages[start:start + limit]
            return list(reversed(selected)), len(all_messages), start

    async def channel_for_admin(self, admin_external_id: str, channel_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(Channel).join(Admin).where(Channel.id == channel_id, Admin.external_id == admin_external_id))

    async def mark_feed_delivered(self, message_id: str):
        async with self.session_factory() as db:
            message = await db.get(Message, message_id)
            if message:
                message.admin_feed_delivered = True
                await db.commit()

    async def get_channel_by_external(self, external_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(Channel).where(Channel.external_id == external_id))

    async def get_channel_owner_external(self, channel_id: str) -> str | None:
        async with self.session_factory() as db:
            row = await db.execute(select(Admin.external_id).join(Channel, Channel.admin_id == Admin.id).where(Channel.id == channel_id))
            return row.scalar_one_or_none()

    async def ensure_conversation(self, channel_id: str, user_id: str, user_name: str = "Пользователь"):
        async with self.session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.channel_id == channel_id, Conversation.user_external_id == user_id))
            if not conversation:
                conversation = Conversation(channel_id=channel_id, user_external_id=user_id, user_name=user_name)
                db.add(conversation)
                await db.commit()
            return conversation.id

    async def ensure_start_conversation(self, channel_id: str, user_id: str, user_name: str = "Пользователь", avatar_url: str | None = None) -> bool:
        """Create the first deep-link conversation and report whether it was new."""
        async with self.session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(
                Conversation.channel_id == channel_id,
                Conversation.user_external_id == user_id,
            ))
            if conversation:
                if avatar_url and conversation.avatar_url != avatar_url:
                    conversation.avatar_url = avatar_url
                    await db.commit()
                return False
            db.add(Conversation(channel_id=channel_id, user_external_id=user_id, user_name=user_name, avatar_url=avatar_url))
            await db.commit()
            return True

    async def has_external_message(self, external_message_id: str | None) -> bool:
        if not external_message_id:
            return False
        async with self.session_factory() as db:
            return bool(await db.scalar(select(Message.id).where(Message.external_message_id == external_message_id)))

    async def create_incoming(self, channel: Channel, user_id: str, user_name: str, text: str, external_message_id: str | None = None):
        async with self.session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.channel_id == channel.id, Conversation.user_external_id == user_id))
            if not conversation:
                conversation = Conversation(channel_id=channel.id, user_external_id=user_id, user_name=user_name)
                db.add(conversation)
                await db.flush()
            existing = await db.scalar(select(Message).where(Message.external_message_id == external_message_id)) if external_message_id else None
            if existing:
                return conversation.id
            conversation.unread += 1
            db.add(Message(conversation_id=conversation.id, direction="in", text=text, external_message_id=external_message_id))
            conversation.last_message_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
            await db.commit()
            return conversation.id

    async def create_incoming_once(self, channel: Channel, user_id: str, user_name: str, text: str, external_message_id: str | None = None, avatar_url: str | None = None, attachments: list[dict] | None = None, is_self_test: bool = False):
        """Store one incoming message and report whether it was newly created."""
        async with self.session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(
                Conversation.channel_id == channel.id,
                Conversation.user_external_id == user_id,
            ))
            if not conversation:
                conversation = Conversation(channel_id=channel.id, user_external_id=user_id, user_name=user_name, avatar_url=avatar_url)
                db.add(conversation)
                await db.flush()
            elif avatar_url and conversation.avatar_url != avatar_url:
                conversation.avatar_url = avatar_url
            if external_message_id:
                duplicate = await db.scalar(select(Message.id).where(Message.external_message_id == external_message_id))
            else:
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
                duplicate = await db.scalar(select(Message.id).join(Conversation, Message.conversation_id == Conversation.id).where(
                    Conversation.channel_id == channel.id,
                    Message.direction == "in",
                    Message.text == text,
                    Message.created_at >= cutoff,
                ))
            if duplicate:
                return conversation.id, False, None
            conversation.unread += 1
            message = Message(
                conversation_id=conversation.id,
                direction="in",
                text=text,
                attachments_json=json.dumps(attachments or [], ensure_ascii=False),
                external_message_id=external_message_id,
                is_self_test=is_self_test,
            )
            db.add(message)
            conversation.last_message_at = datetime.now(timezone.utc)
            await db.commit()
            return conversation.id, True, message.id

    async def feed_message(self, message_id: str):
        async with self.session_factory() as db:
            return await db.scalar(select(Message).options(selectinload(Message.conversation)).where(Message.id == message_id))
