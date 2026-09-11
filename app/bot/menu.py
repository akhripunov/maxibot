from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
import re
import socket
import secrets
from zoneinfo import ZoneInfo
from urllib.parse import quote, unquote
from sqlalchemy import delete, exists, func, insert, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Admin, Channel, ChannelSlugRegistry, Conversation, Message, BlockedUser, AdminFeedMessage, SupportTicket, SupportTicketMessage, BotConnection, CustomBotSession, CustomBotMessage, LandingPage, TrafficProfile, SystemPromotionProfile, BotPromotionProfile, BotTrafficVisit, BotMaxIdentityLink, BotTrafficEvent, BotConversion, TrafficVisit, MembershipEvent, TrafficMembership, TrafficEvent, TrafficAttributionCandidate, MaxIdentityLink, TrafficSubscription, OfflineConversion, MaxJoinEvent, SubscriptionOrder, FreeTrial
from app.traffic.attribution import decide_scored, HIGH, MEDIUM, EXACT, can_send_offline_conversion, resolve_exact_identity_visit
from app.domain.services import menu_text, WELCOME, SENT, editable_welcome, normalize_channel_welcome, normalize_channel_sent, promotion_landing_description
from app.core.config import settings
from app.traffic.deep_link import valid_max_target
from app.integrations.yandex_oauth import authorize_url, configured, connection_status, create_state, get_encrypted_token, system_connection_status
from app.integrations.yandex_metrica import channel_setup_status, ensure_channel_goals_for_existing_counter, YandexMetricaClient
from app.security_tokens import decrypt_token
from app.traffic.metrica_sender import send_user_subscribed, send_user_unsubscribed
from app.integrations.max_connection import PRIMARY, BOT_CONNECTION
from app.traffic.join_intent import consume_single_active_join_intent
from app.traffic.analytics import traffic_stats_for_channels
from app.traffic.bot_start_observability import dropoff_classification, start_wait_timeout_reached
from app.traffic.bot_conversion_dedupe import paid_bot_conversion_key
from app.ads.service import can_access_ads, campaigns_for_channel, latest_view, campaigns_for_promotion_object
from app.ads.dashboard import build_ads_dashboard
from app.ads.objectives import active_links, objective_for_link
from app.ads.analysis import build_report_view, dedupe_trend_findings
from app.ads.memory import set_status
from app.db.models import AdsAiOutcome, AdsAiRun, AdsMemory, AdsRecommendation
from app.ads.storage_health import TABLES, diagnostics as storage_diagnostics, human_size, ui_time
from app.integrations.robokassa import payment_url, amount_for_months, TARIFFS, DISCOUNTS
from app.integrations.subscriptions import subscription_status, free_trial_status, channel_promotion_entitlement as resolve_channel_promotion_entitlement

logger = logging.getLogger(__name__)

MAXIBOT_CHANNEL_URL = "https://max.ru/channel_maxibot"
MOSCOW = ZoneInfo("Europe/Moscow")
YANDEX_UTM_TEMPLATE = "utm_source=yandex&utm_medium=cpc&utm_campaign={campaign_name_lat}.{campaign_id}.{gbid}&utm_content={creative_id}.{ad_id}.{retargeting_id}&utm_term={keyword}"
PROMOTION_VIDEO_URL = f"{settings.traffic_public_base_url.rstrip('/')}/video-instruction"

# AI output is evidence-backed, but human-readable fields are still
# untrusted text at the MAX message boundary.  Keep provenance in the stored
# validated result while preventing arbitrary external URLs from becoming
# rich previews in the user-facing AI card.
EXTERNAL_LINKS_IN_AI_RESULT = "NOT_ALLOWED"
_AI_MARKDOWN_EXTERNAL_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*(?:(?:https?://|www\.)[^)]+|(?:[a-z0-9-]+\.)+[a-z]{2,63}[^)]*)\)", re.IGNORECASE)
_AI_EXTERNAL_URL_RE = re.compile(r"(?<![\w@])(?:https?://|www\.)[^\s<>()\]]+", re.IGNORECASE)
_AI_BARE_DOMAIN_RE = re.compile(r"(?<![\w@])(?:[a-z0-9-]+\.)+[a-z]{2,63}(?:/[^\s<>()\]]*)?(?![\w.-])", re.IGNORECASE)


def sanitize_ai_user_text(value: object) -> str:
    """Remove external URL syntax from AI prose without mutating provenance."""
    text = str(value or "—")
    text = _AI_MARKDOWN_EXTERNAL_LINK_RE.sub(r"\1", text)
    text = _AI_EXTERNAL_URL_RE.sub("", text)
    text = _AI_BARE_DOMAIN_RE.sub("внешний источник", text)
    # Avoid leaving awkward whitespace after removing a URL, while retaining
    # the original wording and all non-URL evidence references.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip() or "—"


def support_profile_url() -> str | None:
    """Return an HTTPS personal-profile link accepted by MAX buttons."""
    url = (settings.maxibot_support_url or "").strip()
    return url if url.startswith(("https://", "http://")) else None


def support_button() -> dict:
    """Build the support control: direct owner chat, with legacy fallback."""
    url = support_profile_url()
    return ({"text": "💬 Написать в поддержку", "type": "link", "url": url}
            if url else {"text": "💬 Написать в поддержку", "callback_data": "support_start"})


def is_maxibot_owner(admin: Admin) -> bool:
    """Expose system MAXIBOT promotion only to its configured owner."""
    owner_id = (settings.maxibot_owner_user_id or "").strip()
    return bool(owner_id and owner_id == str(admin.external_id))


def channel_connection_instructions() -> str:
    """Single canonical copy used by every channel-connect entry point."""
    return (
        "📢 Подключение канала\n\n"
        "Чтобы подключить канал, выполните следующие шаги:\n\n"
        "1. Откройте настройки вашего канала\n"
        "2. Перейдите в раздел «Подписчики»\n"
        "3. Нажмите «Добавить подписчиков»\n"
        "4. Найдите в поиске «MAXIBOT» и добавьте\n"
        "5. Перейдите в раздел «Администраторы» и добавьте «MAXIBOT» в администраторы канала"
    )


def promotion_intro_text() -> str:
    """Canonical promotion explanation shown before channel selection."""
    return (
        "📖 **Как запустить рекламу**\n\n"
        "1. Выберите нужный канал из списка ниже.\n"
        "2. Откройте его карточку и скопируйте ссылку для Директа.\n"
        "3. Нажмите кнопку «Яндекс.Директ».\n"
        "4. Авторизуйтесь в аккаунте Яндекс и разрешите доступ.\n"
        "5. Создайте рекламную кампанию в Яндекс.Директе.\n"
        "6. Вставьте ссылку на лендинг в поле «Ссылка на сайт».\n"
        "7. Выберите счётчик Яндекс.Метрики, созданный MAXIBOT для этого канала, и конверсию **`userDidSubscribe`**.\n"
        "8. Добавьте к ссылке UTM-метку:\n\n"
        f"{YANDEX_UTM_TEMPLATE}\n"
        "\n9. Запустите рекламу.\n\n"
        "⚠️ Используйте именно ссылку на лендинг канала, а не прямую ссылку на канал.\n\n"
        f"🎥 [Видеоинструкция по настройке рекламы]({PROMOTION_VIDEO_URL})\n\n"
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
        "4. Найдите в поиске «MAXIBOT» и добавьте\n"
        "5. Перейдите в раздел «Администраторы» и добавьте «MAXIBOT» в администраторы канала"
    )


async def _send_exact_conversion(db: AsyncSession, conversion: OfflineConversion, membership: TrafficMembership, channel: Channel) -> str:
    """Deliver one exact conversion immediately after official user_added.

    The caller has already committed the exact identity link and outbox row.
    This function is deliberately fail-closed: only an EXACT row with a yclid
    is sent, and failures leave a retryable non-sent status without affecting
    membership attribution.
    """
    if settings.yandex_conversion_dry_run:
        return "dry_run"
    if membership.attribution_status != EXACT or not conversion.identifier_value:
        return "blocked"
    client = None
    try:
        visit = await db.get(TrafficVisit, conversion.visit_id)
        # Yandex offline conversions are keyed by yclid. Older queue logic
        # could create a row from ClientID alone, but the sender rejects that
        # payload. Mark such legacy rows explicitly instead of leaving them
        # looking retryable forever.
        if not visit or not visit.yclid:
            conversion.status = "blocked"
            conversion.last_error = "yclid_required"
            await db.commit()
            return "blocked"
        metrica_hit_confirmed = bool(visit and await db.scalar(select(TrafficEvent.id).where(
            TrafficEvent.visit_id == visit.id,
            TrafficEvent.event_type == "METRICA_HIT_CONFIRMED",
        )))
        if not visit or not metrica_hit_confirmed or not visit.metrica_client_id:
            conversion.status = "blocked"
            conversion.last_error = "metrica_visit_unconfirmed"
            await db.commit()
            return "blocked"
        admin_external_id = await db.scalar(select(Admin.external_id).where(Admin.id == channel.admin_id))
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id))
        setup = await channel_setup_status(admin_external_id, channel.id)
        counter_value = (profile.yandex_counter_id if profile else None) or setup.get("counter_id")
        if not counter_value or not str(counter_value).isdigit():
            conversion.status = "blocked"
            conversion.last_error = "yandex_counter_missing"
            await db.commit()
            return "blocked"
        encrypted_token = await get_encrypted_token(admin_external_id, channel.id, db=db)
        if not encrypted_token:
            conversion.status = "blocked"
            conversion.last_error = "yandex_oauth_missing"
            await db.commit()
            return "blocked"
        counter_id = int(counter_value)
        client = YandexMetricaClient(decrypt_token(encrypted_token))
        sender = send_user_unsubscribed if conversion.goal == "userDidUnsubscribe" else send_user_subscribed
        result = await sender(
            client,
            counter_id=counter_id,
            yclid=visit.yclid,
            client_id=visit.metrica_client_id,
            # Yandex Offline Conversions CSV requires DateTime as a Unix
            # timestamp, not an ISO-8601 string.
            conversion_datetime=str(int(conversion.conversion_datetime.timestamp())),
            verified_membership=True,
        )
        if result.status == "sent":
            uploading = (result.response or {}).get("uploading") or {}
            upload_id = uploading.get("id")
            if upload_id is None:
                # Without the asynchronous upload id the worker cannot
                # verify PROCESSED vs LINKAGE_FAILURE. Keep the row retryable
                # instead of presenting an unverifiable upload as success.
                conversion.status = "pending"
                conversion.last_error = "metrica_upload_id_missing"
                conversion.attempts = int(conversion.attempts or 0) + 1
                await db.commit()
                return "pending"
            conversion.status = "uploaded"
            conversion.metrica_upload_id = str(upload_id)
            conversion.metrica_upload_status = str(uploading.get("status") or "UPLOADED").upper()
            conversion.metrica_upload_checked_at = datetime.now(timezone.utc)
            conversion.attempts = int(conversion.attempts or 0) + 1
            conversion.last_error = None
            await db.commit()
        return result.status
    except Exception as exc:
        logger.warning("exact_conversion_send_failed category=%s", type(exc).__name__)
        conversion.status = "pending"
        conversion.attempts = int(conversion.attempts or 0) + 1
        conversion.last_error = type(exc).__name__
        await db.commit()
        return "failed"
    finally:
        if client:
            await client.close()


def home_menu_banner_media() -> list[dict]:
    """Return the single decorative attachment used by the home screen.

    The URL is derived from the configured public traffic host so staging and
    production never cross-load one another's asset.  Other menu screens must
    remain text-only.
    """
    asset_url = f"{settings.traffic_public_base_url.rstrip('/')}/static/images/maxibot_home_banner_fit.webp"
    return [{"type": "image", "payload": {"url": asset_url}}]


def _time(value) -> str:
    if not value:
        return ""
    normalized = value.astimezone(timezone.utc) if value.tzinfo else value
    return normalized.strftime("%H:%M")


def _ru_number(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    formatted = f"{number:,.{decimals}f}"
    return formatted.replace(",", "\u0000").replace(".", ",").replace("\u0000", " ")


def _metric(key: str, value) -> str:
    if value is None:
        return "—"
    if key in {"cost_7d", "actual_cpa_7d"}:
        return f"{_ru_number(value)} ₽"
    if key == "conversions_7d":
        return _ru_number(value, 0)
    if key == "maxibot_qualified_membership_rate":
        return f"{_ru_number(float(value) * 100)}%"
    return _ru_number(value)


def format_trend_block(trends: dict | None, campaign_name: str | None = None) -> str:
    """Render at most three deterministic, evidence-backed trend findings."""
    findings = (trends or {}).get("findings") or []
    if not findings:
        return ""
    states = {"IMPROVING": "улучшается", "STABLE": "стабильно", "DEGRADING": "ухудшается", "VOLATILE": "нестабильно", "RECOVERED": "восстановилось", "NEW_SIGNAL": "новый сигнал"}
    metrics = {"cpa": "CPA", "cvr": "CVR", "cpc": "CPC", "conversions": "конверсии"}
    selected_findings = dedupe_trend_findings(findings)
    lines = []
    for finding in selected_findings[:3]:
        state = states.get(finding.get("trend_state"), "мало истории")
        metric = metrics.get(finding.get("metric"), finding.get("metric", "метрика"))
        labels = {"CAMPAIGN": campaign_name or "кампания", "AD": "объявление", "AD_GROUP": "группа объявлений", "PLACEMENT": "площадка", "REGION": "регион", "DEVICE": "устройство", "DEMOGRAPHICS": "демография", "CRITERIA": "критерий"}
        entity = safe_max_entity_label(humanize_entity_label(finding.get("dimension"), finding.get("entity")))
        if finding.get("dimension") == "CAMPAIGN":
            entity = "кампании"
        if not entity or entity.isdigit() or (len(entity) > 40 and entity.count("-") >= 2):
            entity = labels.get(finding.get("dimension"), "сущность")
        relative = finding.get("relative_delta")
        change = f" ({_signed_ru_percent(relative)})" if relative is not None else ""
        lines.append(f"• {metric} {entity}: {state}{change}")
    return "📈 Динамика\n" + "\n".join(lines) + "\n"


def _format_finding_value(value, decimals: int = 2) -> str:
    return _ru_number(value, decimals)


def humanize_entity_label(dimension: str | None, value: object) -> str:
    """Convert provider identifiers to stable Russian labels at the UI edge."""
    raw = str(value or "")
    if str(dimension or "").upper() == "DEMOGRAPHICS":
        age_match = re.search(r"AGE_([0-9]+)(?:_([0-9]+)|_PLUS|\+)?", raw)
        gender = re.search(r"GENDER_([A-Z]+)", raw)
        gender_label = {"FEMALE": "Женщины", "MALE": "Мужчины"}.get(gender.group(1), "Пользователи") if gender else "Пользователи"
        if age_match:
            low, high = age_match.group(1), age_match.group(2)
            age = f"{low}–{high}" if high else f"{low}+"
            return f"{gender_label} {age}"
        return "Демографический сегмент"
    mappings = {
        "DEVICE": {"MOBILE": "Мобильные устройства", "DESKTOP": "Компьютеры", "TABLET": "Планшеты"},
        "REGION": {"RU": "Россия"},
    }
    return mappings.get(str(dimension or "").upper(), {}).get(raw, raw)


_DOMAIN_LABEL_RE = re.compile(r"(?<![\w])([a-z0-9-]+)\.([a-z]{2,63})(?![\w.-])", re.IGNORECASE)


def safe_max_entity_label(label: object) -> str:
    """Keep placement names recognizable while preventing MAX auto-linking."""
    text = str(label or "")
    if re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,63}", text, flags=re.IGNORECASE):
        return text.replace(".", "[.]")
    return _DOMAIN_LABEL_RE.sub(r"\1[.]\2", text)


def _count_phrase(value: int, one: str, few: str, many: str) -> str:
    value = int(value)
    mod100 = value % 100
    form = many if 11 <= mod100 <= 14 else one if value % 10 == 1 else few if value % 10 in {2, 3, 4} else many
    return f"{value} {form}"


def _signed_ru_percent(value: object) -> str:
    try:
        return f"{float(value) * 100:+.1f}".replace(".", ",") + "%"
    except (TypeError, ValueError):
        return "—"


def format_primary_findings(v2_analysis: dict | None) -> str:
    """Render material deterministic findings with grounded entity labels."""
    findings = build_report_view(v2_analysis or {}).get("primary_findings") or []
    lines = []
    for finding in findings[:3]:
        dimension = finding.get("dimension")
        label = safe_max_entity_label(humanize_entity_label(dimension, finding.get("label") or finding.get("entity") or "сущность"))
        metrics = finding.get("metrics") or {}
        cpa = metrics.get("cpa")
        campaign_cpa = finding.get("campaign_cpa")
        prefix = {"PLACEMENT": "Площадка", "AD": "Объявление", "AD_GROUP": "Группа объявлений"}.get(dimension, dimension or "Сигнал")
        if finding.get("finding_type") == "HIGH_CPA" and cpa is not None and campaign_cpa is not None:
            detail = f"CPA {_format_finding_value(cpa)} ₽ против {_format_finding_value(campaign_cpa)} ₽ по кампании"
            if metrics.get("cost_share") is not None:
                detail += f", {_format_finding_value(float(metrics['cost_share']) * 100)}% расходов"
            lines.append(f"• {prefix} {label} — под наблюдением: {detail}.")
        elif finding.get("finding_type") == "STRONG_PERFORMANCE" and cpa is not None:
            lines.append(f"• {prefix} {label} — положительный сигнал: CPA {_format_finding_value(cpa)} ₽.")
        elif finding.get("sample_sufficiency") == "WEAK_SIGNAL":
            lines.append(f"• {prefix} {label} — слабый сигнал, данных пока недостаточно.")
    return "🔎 Что выделяется\n" + "\n".join(lines) + "\n" if lines else ""


def format_dimension_coverage(v2_analysis: dict | None) -> str:
    """Show available/included dimension counts without raw JSON."""
    comparison = build_report_view(v2_analysis or {}).get("comparison") or {}
    labels = {"ad_group": "Группы", "ad": "Объявления", "placement": "Площадки", "region": "Регионы", "device": "Устройства", "demographics": "Демография", "criteria": "Критерии"}
    parts = []
    for key in ("ad_group", "ad", "placement", "region", "device", "demographics", "criteria"):
        item = comparison.get(key) or {}
        if item.get("available"):
            available = item.get("available")
            selected = item.get("included")
            material = item.get("material_findings_count", item.get("material_findings"))
            weak = item.get("weak_signals_count", item.get("weak_signals"))
            trend = item.get("trend_signals_count", item.get("trend_signals"))
            detail = f"доступно {available}"
            if selected not in (None, 0):
                detail += f" · отобрано {selected}"
            if material not in (None, 0):
                detail += f" · {_count_phrase(material, 'значимый вывод', 'значимых вывода', 'значимых выводов')}"
            elif material == 0:
                detail += " · значимых текущих выводов нет" if trend not in (None, 0) else " · значимых выводов нет"
            if weak not in (None, 0):
                detail += f" · {_count_phrase(weak, 'слабый сигнал', 'слабых сигнала', 'слабых сигналов')}"
            if trend not in (None, 0):
                detail += f" · {_count_phrase(trend, 'тренд-сигнал', 'тренд-сигнала', 'тренд-сигналов')}"
            if key == "ad" and item.get("state") == "SINGLE_ENTITY":
                detail += " · сравнивать креативы пока не с чем"
            parts.append(f"• {labels[key]}: {detail}")
    return "🧭 Что проверил ИИ-директолог\n" + "\n".join(parts) + "\n" if parts else ""


def normalize_ai_text(text: str, fact_values: dict) -> str:
    """Normalize legacy persisted summaries at display time only."""
    if not text:
        return text
    for technical, readable in (("performance_confidence", "уверенность в данных Яндекса"), ("WEAK_SIGNAL", "слабый сигнал"), ("VOLATILE", "нестабильно")):
        text = text.replace(technical, readable)
    text = re.sub(r"attribution_confidence\s*[:=]?\s*(LOW|MEDIUM|HIGH|INSUFFICIENT)", lambda match: f"уверенность в сопоставлении MaxiBot: {({'LOW': 'низкая', 'MEDIUM': 'средняя', 'HIGH': 'высокая', 'INSUFFICIENT': 'недостаточно данных'}.get(match.group(1).upper(), match.group(1)))}", text, flags=re.IGNORECASE)
    text = re.sub(r"\bconfidence\b", "уверенность", text, flags=re.IGNORECASE)
    text = re.sub(r"\breview\b", "проверка", text, flags=re.IGNORECASE)
    text = text.replace("здоровый результат", "достаточные результаты")
    text = text.replace("при высокой уверенность", "при высокой уверенности")
    text = text.replace("при высокой уверенности в данных Яндекса", "при высокой уверенности в данных Яндекса")
    text = re.sub(r"\bread-only\b", "проверку", text, flags=re.IGNORECASE)
    if "атрибуция maxibot ограничена" not in text.lower():
        return text
    qualified = fact_values.get("qualified_subscriptions")
    visits = fact_values.get("landing_visits")
    rate = fact_values.get("maxibot_qualified_membership_rate")
    if rate is None and qualified is not None and visits:
        rate = qualified / visits
    if qualified is None or visits is None or rate is None:
        return text.replace("Атрибуция MaxiBot ограничена", "Campaign-specific сопоставление MaxiBot ограничено")
    canonical = (
        f"Данные MaxiBot: {_ru_number(qualified, 0)} подтверждённых подписок из "
        f"{_ru_number(visits, 0)} визитов; Конверсия визит → подписка: "
        f"{_metric('maxibot_qualified_membership_rate', rate)}. "
        "Сопоставление с конкретной рекламной кампанией недоступно"
    )
    return re.sub(r"Атрибуция MaxiBot ограничена:.*?(?=\.\s|$)", canonical, text, count=1)


def _user_facing_ai_text(text: str, v2_analysis: dict | None = None) -> str:
    """Финальный безопасный и локализованный проход для текста AI."""
    rendered = re.sub(r"\bread-only\b", "проверку", sanitize_ai_user_text(text or "—"), flags=re.IGNORECASE)
    rendered = re.sub(r"при высокой уверенность", "при высокой уверенности", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"attribution_confidence\s*[:=]?\s*(LOW|MEDIUM|HIGH|INSUFFICIENT|низкая|средняя|высокая)", lambda match: f"уверенность в сопоставлении MaxiBot: {({'LOW': 'низкая', 'MEDIUM': 'средняя', 'HIGH': 'высокая', 'INSUFFICIENT': 'недостаточно данных'}.get(match.group(1).upper(), match.group(1)))}", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"\bconfidence\b", "уверенность", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"\breview\b", "проверка", rendered, flags=re.IGNORECASE)
    rendered = rendered.replace("здоровый результат", "достаточные результаты")
    primary = build_report_view(v2_analysis or {}).get("primary_findings") or []
    placement = next((item for item in primary if item.get("dimension") == "PLACEMENT"), None)
    if placement:
        label = safe_max_entity_label(humanize_entity_label("PLACEMENT", placement.get("label") or placement.get("entity")))
        rendered = re.sub(r"(?:размещение|площадка)\s+внешний источник", f"площадка {label}", rendered, flags=re.IGNORECASE)
        rendered = rendered.replace("внешний источник", f"площадка {label}")
    return re.sub(r"(CPA\s+\d+(?:[,.]\d+)?)\b(?!\s*₽)", r"\1 ₽", rendered, flags=re.IGNORECASE)


def _structured_summary(v2_analysis: dict | None, fact_values: dict, fallback: str) -> str:
    if _quality_decision_snapshot(v2_analysis):
        parts = []
        conversions = fact_values.get("conversions_7d")
        cpa = fact_values.get("actual_cpa_7d")
        if conversions is not None and cpa is not None:
            parts.append(f"По данным Яндекса кампания показывает {_ru_number(conversions, 0)} конверсий за 7 дней при CPA {_ru_number(cpa)} ₽.")
        return "\n\n".join(parts) or "Общая оценка кампании сформирована по доступным данным."
    view = build_report_view(v2_analysis or {})
    findings = view.get("primary_findings") or []
    placement = next((item for item in findings if item.get("dimension") == "PLACEMENT"), None)
    if not placement:
        return _user_facing_ai_text(fallback, v2_analysis)
    metrics = placement.get("metrics") or {}
    label = safe_max_entity_label(humanize_entity_label("PLACEMENT", placement.get("label") or placement.get("entity")))
    cpa = metrics.get("cpa")
    baseline = placement.get("campaign_cpa")
    conversions = fact_values.get("conversions_7d")
    parts = []
    if conversions is not None and fact_values.get("actual_cpa_7d") is not None:
        parts.append(f"По данным Яндекса кампания показывает {_ru_number(conversions, 0)} конверсий за 7 дней при CPA {_ru_number(fact_values['actual_cpa_7d'])} ₽.")
    if cpa is not None and baseline is not None:
        share = f", доля расходов {_ru_number(float(metrics.get('cost_share')) * 100)}%" if metrics.get("cost_share") is not None else ""
        parts.append(f"Главный сигнал для проверки — площадка {label}: CPA {_ru_number(cpa)} ₽ против {_ru_number(baseline)} ₽ по кампании{share}.")
    return "\n\n".join(parts) or _user_facing_ai_text(fallback, v2_analysis)


def _structured_recommendation(v2_analysis: dict | None, fallback: str) -> str:
    snapshot = _quality_decision_snapshot(v2_analysis)
    if snapshot:
        candidate = snapshot.get("primary_candidate") or {}
        action = _QUALITY_ACTION_LABELS.get(str(candidate.get("action_level") or ""))
        if action:
            if action == "Проверить":
                action = "Проверить вручную"
            return f"{action}."
    view = build_report_view(v2_analysis or {})
    placement = next((item for item in (view.get("primary_findings") or []) if item.get("dimension") == "PLACEMENT"), None)
    if not placement:
        return _user_facing_ai_text(fallback, v2_analysis)
    metrics = placement.get("metrics") or {}
    label = safe_max_entity_label(humanize_entity_label("PLACEMENT", placement.get("label") or placement.get("entity")))
    cpa, baseline = metrics.get("cpa"), placement.get("campaign_cpa")
    comparison = f"её CPA {_ru_number(cpa)} ₽ выше среднего по кампании" if cpa is not None and baseline is not None else "по ней есть значимый сигнал для проверки"
    share = f", а доля расходов составляет {_ru_number(float(metrics.get('cost_share')) * 100)}%" if metrics.get("cost_share") is not None else ""
    return f"В первую очередь проверить площадку {label}: {comparison}{share}.\nПока только наблюдать и анализировать — ничего автоматически не исключать и не менять."


_QUALITY_ACTION_LABELS = {
    "WATCH": "Наблюдать",
    "INVESTIGATE": "Проверить",
    "PLACEMENT_REVIEW": "Проверить",
    "CONSIDER_CHANGE": "Можно рассмотреть изменение",
    "STRONG_RECOMMENDATION": "Есть веские основания для изменения",
    "NO_ACTION": "Ничего менять не нужно",
}
_QUALITY_EVOLUTION_LABELS = {
    "NEW": "новый сигнал",
    "PERSISTENT": "сигнал сохраняется",
    "STRENGTHENING": "сигнал усиливается",
    "WEAKENING": "сигнал ослабевает",
    "RESOLVED": "сигнал разрешился",
}


def _quality_decision_snapshot(v2_analysis: dict | None) -> dict | None:
    snapshot = (v2_analysis or {}).get("QUALITY_DECISION_SNAPSHOT")
    if isinstance(snapshot, dict) and isinstance(snapshot.get("primary_candidate"), dict):
        return snapshot
    return None


def _format_quality_decision_snapshot(snapshot: dict | None) -> str:
    if not snapshot:
        return ""
    candidate = snapshot.get("primary_candidate") or {}
    action = _QUALITY_ACTION_LABELS.get(str(candidate.get("action_level") or ""))
    if not action:
        return ""
    lines = []
    deviation = candidate.get("relative_deviation")
    spend_share = candidate.get("spend_share")
    if deviation is not None:
        deviation_text = f"{_ru_number(abs(float(deviation)) * 100)}%"
        lines.append(f"Почему это важно: CPA отклоняется от среднего по кампании на {deviation_text}.")
    if spend_share is not None:
        lines.append(f"Доля расходов по сигналу: {_ru_number(float(spend_share) * 100)}%.")
    for key in ("WHY_THIS", "WHY_NOW"):
        value = candidate.get(key) or candidate.get(key.lower())
        if value and not str(value).startswith(("Сигнал основан на сравнении", "Есть достаточная выборка")):
            lines.append(str(value))
    if deviation is not None or spend_share is not None:
        lines.append("Почему сейчас: текущие метрики дают проверяемый сигнал.")
    sufficiency = candidate.get("data_sufficiency")
    if sufficiency == "SUFFICIENT":
        lines.append("Выборки достаточно для проверки сигнала.")
    elif sufficiency in {"WEAK_SIGNAL", "PARTIAL"}:
        lines.append("Данные дают сигнал, но требуют дополнительной проверки.")
    elif sufficiency not in {None, "UNKNOWN", "INSUFFICIENT"}:
        lines.append("Достаточность данных не подтверждена.")
    next_step = candidate.get("NEXT_STEP") or candidate.get("next_step")
    if next_step:
        cleaned_next_step = re.sub(r"[.;]?\s*автоматические изменения отключены\.?", "", str(next_step), flags=re.IGNORECASE).strip()
        if cleaned_next_step:
            lines.append(cleaned_next_step)
    wait = candidate.get("WAIT_CONDITION") or candidate.get("wait_condition")
    if wait:
        lines.append(f"Условие ожидания: {wait}")
    evolution_code = str(candidate.get("signal_evolution") or "")
    evolution = _QUALITY_EVOLUTION_LABELS.get(evolution_code) if evolution_code in {"PERSISTENT", "STRENGTHENING", "WEAKENING", "RESOLVED"} else None
    if evolution and snapshot.get("history_state") != "UNKNOWN":
        if evolution_code == "STRENGTHENING":
            lines.append("Сигнал усиливается по сравнению с прошлой проверкой.")
        else:
            lines.append(f"Динамика сигнала: {evolution}")
    outcome = snapshot.get("outcome_state")
    if outcome == "PENDING":
        lines.append("Измерение результата ещё ожидается.")
    elif outcome == "COMPLETED":
        lines.append("Измеренный результат доступен.")
    lines.append("Автоматические изменения отключены.")
    return "\n".join(lines)


def format_ads_result(*, headline: str, channel_title: str, direct_connected: bool, campaign_name: str | None, ai_status: str, checked_at, fact_values: dict, diagnosis: str = "—", recommendation: str = "—", confidence: str = "—", performance_confidence: str = "—", attribution_confidence: str = "—", completed: bool = False, trends: dict | None = None, campaign_specific_matching: str | bool = "UNKNOWN", v2_analysis: dict | None = None) -> str:
    """Single user-facing renderer for every AI dashboard/result path."""
    checked = checked_at.astimezone(MOSCOW).strftime("%d.%m %H:%M") if checked_at else "—"
    confidence_label = {"LOW": "низкая", "MEDIUM": "средняя", "HIGH": "высокая"}.get(confidence, confidence)
    result = (
        f"{headline}\n\n📢 Канал: {channel_title}\n\n"
        f"Яндекс.Директ: {'✅ Подключён' if direct_connected else '⚠️ Не подключён'}\n"
        f"Кампания: {campaign_name or 'Не выбрана'}\n"
        f"AI-анализ: {ai_status}\nПоследняя проверка: {checked}\n\n"
    )
    if completed:
        qualified = fact_values.get("qualified_subscriptions")
        visits = fact_values.get("landing_visits")
        rate = fact_values.get("maxibot_qualified_membership_rate")
        if rate is None and qualified is not None and visits:
            rate = qualified / visits
        maxibot_metric = (f"📊 Данные MaxiBot\n{_ru_number(qualified, 0)} подтверждённых подписок из {_ru_number(visits, 0)} визитов\nКонверсия визит → подписка: {_metric('maxibot_qualified_membership_rate', rate)}" if qualified is not None and visits is not None else f"📊 Данные MaxiBot\nКонверсия визит → подписка: {_metric('maxibot_qualified_membership_rate', rate)}")
        campaign_scope = "недоступно" if campaign_specific_matching in (False, "UNKNOWN", None) else "доступно"
        result += (
            f"Расход 7 дней: {_metric('cost_7d', fact_values.get('cost_7d'))}\n"
            f"Конверсии: {_metric('conversions_7d', fact_values.get('conversions_7d'))}\n"
            f"CPA: {_metric('actual_cpa_7d', fact_values.get('actual_cpa_7d'))}\n"
            f"{maxibot_metric}\nСопоставление с конкретной рекламной кампанией: {campaign_scope}\n\n"
            f"🤖 Вывод AI\n{_structured_summary(v2_analysis, fact_values, diagnosis)}\n\n{format_primary_findings(v2_analysis)}{format_dimension_coverage(v2_analysis)}{format_trend_block(trends, campaign_name)}\nРекомендация:\n{_structured_recommendation(v2_analysis, recommendation)}\n{_format_quality_decision_snapshot(_quality_decision_snapshot(v2_analysis))}\n"
            f"Уверенность: {confidence_label}\n"
            f"Уверенность в данных Яндекса: {({'LOW': 'низкая', 'MEDIUM': 'средняя', 'HIGH': 'высокая'}.get(performance_confidence, performance_confidence))}\n"
            f"Уверенность в сопоставлении MaxiBot: {({'LOW': 'низкая', 'MEDIUM': 'средняя', 'HIGH': 'высокая', 'INSUFFICIENT': 'недостаточно данных'}.get(attribution_confidence, attribution_confidence))}\n\n"
        )
    return result + "🔒 Теневой режим\nAI анализирует и советует, но ничего автоматически не изменяет."


def _owner_short_text(value: object, fallback: str = "—", limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip()
    if not text:
        return fallback
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    return (first_sentence[:limit - 1].rstrip() + "…") if len(first_sentence) >= limit else first_sentence


def _owner_confidence(value: object) -> str:
    return {"LOW": "низкая", "MEDIUM": "средняя", "HIGH": "высокая", "INSUFFICIENT": "недостаточно данных"}.get(str(value or ""), "не определена")


def _safe_json_object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _data_trust_explanation(state: dict) -> str | None:
    """Explain only actionable data-quality limits in client language."""
    status = str(state.get("data_trust") or "UNKNOWN").upper()
    if status == "HEALTHY":
        return None
    reasons = state.get("data_trust_reasons") or {}
    checks = reasons.get("checks") if isinstance(reasons, dict) else {}
    checks = checks if isinstance(checks, dict) else {}
    campaign_mapping = state.get("campaign_specific") or {}
    mapping_supported = isinstance(campaign_mapping, dict) and campaign_mapping.get("supported") is True
    explanations = (
        ("ATTRIBUTION", "Часть результатов пока нельзя надёжно связать с конкретной рекламной кампанией."),
        ("UNMATCHED_ATTRIBUTION", "Часть переходов пока нельзя надёжно связать с конкретной рекламной кампанией."),
        ("CAMPAIGN_MAPPING", "Рекламные данные пока нельзя надёжно сопоставить с нужной кампанией."),
        ("SNAPSHOT_FRESHNESS", "Рекламные данные обновляются с задержкой, поэтому последние показатели могут быть неполными."),
        ("DIRECT_SYNC", "Данные рекламной системы ещё не синхронизированы полностью."),
        ("AI_PROVIDER", "Автоматический анализ временно недоступен, поэтому новая рекомендация ещё не подтверждена."),
        ("MEMORY_DATABASE", "История прошлых проверок временно недоступна для сравнения."),
    )
    for key, message in explanations:
        if mapping_supported and key in {"ATTRIBUTION", "UNMATCHED_ATTRIBUTION", "CAMPAIGN_MAPPING"}:
            continue
        if str(checks.get(key) or "").upper() in {"DEGRADED", "UNHEALTHY", "UNKNOWN"}:
            return f"{message} Поэтому рекомендация предварительная."
    if mapping_supported and not any(
        str(checks.get(key) or "").upper() in {"DEGRADED", "UNHEALTHY", "UNKNOWN"}
        for key in checks if key not in {"ATTRIBUTION", "UNMATCHED_ATTRIBUTION", "CAMPAIGN_MAPPING"}
    ):
        return None
    if status in {"DEGRADED", "UNHEALTHY", "UNKNOWN"}:
        return "Часть данных пока нельзя надёжно сопоставить с рекламной кампанией. Поэтому рекомендация предварительная."
    return None


def format_ads_unified_dashboard(state: dict, *, title: str) -> str:
    """Render the same object-level control screen for CHANNEL and BOT."""
    object_type = state["object"]["type"]
    objective_name = "Подписчик (userDidSubscribe)" if object_type == "CHANNEL" else "Запуск бота (bot_started)"
    outcome_label = "Подтверждённых подписчиков" if object_type == "CHANNEL" else "Подтверждённых запусков"
    currency = state.get("currency") or "RUB"
    currency_label = "₽" if currency == "RUB" else currency
    target = state.get("target_cpa")
    actual = state.get("actual_business_cpa")
    target_text = f"{target:.0f} {currency_label}" if target is not None else "не задан"
    actual_text = f"{actual:.0f} {currency_label}" if actual is not None else "пока не рассчитан"
    stages = ("Анализ", "Рекомендация", "Проверка", "Результат", "Масштабирование")
    active_index = {"ANALYSIS": 0, "RECOMMENDATION": 1, "CHECK": 2, "RESULT": 3, "SCALE": 4}.get(
        state.get("current_stage"), 0
    )
    stage_lines = "\n".join(
        f"{'●' if index == active_index else '✓' if index < active_index else '○'} {label}"
        for index, label in enumerate(stages)
    )
    creative = state.get("creative_summary") or {}
    if not creative.get("active"):
        creative_text = "нет активных"
    elif creative.get("attention"):
        creative_text = f"{creative['active']} активных · {creative['attention']} требует внимания"
    else:
        creative_text = f"{creative['active']} активных · всё нормально"
    root = state.get("primary_root_cause")
    if not root:
        root = "Пока недостаточно данных."
    if state.get("root_cause_status") == "NOT_PROVEN" and state.get("current_recommendation"):
        root = "Рекламная причина пока не доказана."
    outcome = state.get("current_outcome")
    result_line = ""
    if state.get("current_stage") == "RESULT" and outcome:
        result_line = "\n\n✅ Изменение помогло" if outcome.evaluation_status == "SUCCESS" else "\n\n❌ Изменение не помогло"
    elif state.get("current_stage") == "SCALE":
        result_line = "\n\n🚀 Подход подтверждён."
    refresh_line = "\nОбновляем..." if state.get("analysis_running") else ""
    updated = state.get("last_updated_at")
    updated_text = updated.astimezone(MOSCOW).strftime("%d.%m %H:%M") if updated else "ещё не выполнялось"
    confirmed = state.get("confirmed_business_outcomes")
    return (
        f"🤖 AI Продвижение\n\n{'Канал' if object_type == 'CHANNEL' else 'Бот'}: {title}\n"
        f"Цель: {objective_name}\n🎯 Target CPA: {target_text}\n"
        f"Фактический CPA: {actual_text}\n{outcome_label}: {confirmed if confirmed is not None else 'пока нет данных'}\n\n"
        f"🎨 Креативы: {creative_text}\n\nСейчас:\n{stage_lines}{refresh_line}\n\n"
        f"Главная проблема:\n{_owner_short_text(root, 'Пока недостаточно данных.')}\n\n"
        f"Почему:\n{_owner_short_text(state.get('why_summary'), 'MaxiBot ещё не может доказанно определить главную причину.')}\n\n"
        f"➡️ Что делать:\n{_owner_short_text(state.get('next_best_action'), 'Продолжать сбор данных.')}"
        f"{result_line}\n\nОбновлено: {updated_text}"
    )


def ads_unified_buttons(
    action_prefix: str, object_id: str, back_action: str, *, objective_id: str | None = None,
) -> list[list[dict]]:
    """Canonical navigation; the refresh action reuses the bounded pipeline."""
    if action_prefix == "ads_bot":
        routes = {
            "refresh": "ads_bot_check:system",
            "goal": f"ads_objective:BOT:{objective_id or object_id}",
            "campaigns": "ads_bot_campaigns:system",
            "creatives": "ads_bot_creatives:system",
            "why": "ads_bot_context:system",
            "learning": "ads_bot_learning:system",
            "history": "ads_bot_history:system",
        }
    else:
        routes = {
            "refresh": f"ads_check:{object_id}",
            "goal": f"ads_objective:CHANNEL:{object_id}",
            "campaigns": f"ads_campaigns:{object_id}",
            "creatives": f"ads_creatives:{object_id}",
            "why": f"ads_context:{object_id}",
            "learning": f"ads_learning:{object_id}",
            "history": f"ads_history:{object_id}",
        }
    return buttons(
        ("↻ Обновить сейчас", routes["refresh"]), ("🎯 Цель", routes["goal"]),
        ("📊 Кампании", routes["campaigns"]), ("🎨 Креативы", routes["creatives"]),
        ("🔍 Почему", routes["why"]), ("🧠 Обучение", routes["learning"]),
        ("🕘 История", routes["history"]), ("⬅️ Назад", back_action),
    )


def format_ads_campaigns_screen(state: dict, *, title: str) -> str:
    lines = ["📊 Кампании", "", title, ""]
    for item in state.get("active_campaigns") or []:
        status = "🟢 Активна" if item.get("active") else "⚪ Неактивна"
        spend = item.get("spend")
        clicks = item.get("clicks")
        lines.extend([
            f"{item.get('external_id') or item.get('id')} — {status}",
            f"Провайдер: {item.get('provider', 'UNKNOWN')}",
            f"Расход: {_ru_number(spend) + ' ₽' if spend is not None else 'пока нет данных'}",
            f"Клики: {_ru_number(clicks, 0) if clicks is not None else 'пока нет данных'}",
            f"Состояние: {item.get('state') or 'UNKNOWN'}",
        ])
        recommendation = item.get("recommendation")
        if recommendation:
            lines.append(f"Рекомендация: {_owner_short_text(recommendation.title)}")
        lines.append("")
    if len(lines) == 4:
        lines.append("Связанных кампаний пока нет.")
    lines.append("🔒 Только просмотр. Управление Яндекс.Директом отключено.")
    return "\n".join(lines).rstrip()


def format_ads_creatives_screen(state: dict, *, title: str) -> str:
    lines = ["🎨 Креативы", "", title, ""]
    for item in state.get("creatives") or []:
        features = _safe_json_object(item.ai_features_json)
        attention = bool(features.get("requires_attention") and
                         (features.get("evidence_ids") or features.get("reason") or features.get("evidence")))
        label = item.title or item.external_creative_id or item.id
        lines.append(f"• {label} — {'⚠️ Требует внимания' if attention else '✅ Норма'}")
        if attention:
            lines.append(f"  {_owner_short_text(features.get('reason'), 'Есть evidence-backed сигнал.')}")
    if len(lines) == 4:
        lines.append("Активных креативов пока нет в сохранённых данных.")
    lines.extend(["", "Business CPA по креативу показывается только при доказанной атрибуции."])
    return "\n".join(lines)


def format_ads_why_screen(state: dict, *, title: str) -> str:
    root = state.get("primary_root_cause") or "Рекламная причина пока не доказана."
    recommendation = state.get("current_recommendation")
    evidence = []
    if recommendation:
        try:
            parsed = json.loads(recommendation.evidence_ids_json or "[]")
            evidence = parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            evidence = []
    trust_explanation = _data_trust_explanation(state)
    trust_block = f"\n\nО качестве данных:\n{trust_explanation}" if trust_explanation else ""
    return (
        f"🔍 Почему\n\n{title}\n\nГлавная проблема:\n{root}\n\n"
        f"Почему:\n{_owner_short_text(state.get('why_summary'), 'Доказанных сигналов пока нет.')}\n\n"
        f"Уверенность: {_owner_confidence(recommendation.confidence if recommendation else None)}\n"
        f"Evidence: {', '.join(str(item) for item in evidence[:8]) if evidence else 'недостаточно'}\n\n"
        "Provider metrics и подтверждённый бизнес-результат не смешиваются."
        f"{trust_block}"
    )


def format_ads_learning_screen(state: dict, *, title: str) -> str:
    groups = {"EXPLORE": "🧪 Исследуем", "PROMISING": "📈 Перспективно", "PROVEN": "🚀 Доказано", "REJECTED": "⛔ Отклонено"}
    lines = ["🧠 Обучение", "", title, "", f"Состояние: {groups.get(state['learning_state']['status'], state['learning_state']['status'])}"]
    counts = state.get("memory_evidence_counts") or {}
    for memory in state.get("memories") or []:
        group = "PROVEN" if memory.status == "TRUSTED" else "PROMISING"
        lines.extend(["", f"{groups[group]} · {_owner_short_text(memory.content)}",
                      f"Уверенность: {_owner_confidence(memory.confidence)} · evidence: {counts.get(memory.id, 0)}",
                      f"Проверено: {memory.updated_at.astimezone(MOSCOW):%d.%m.%Y}"])
    if not state.get("memories"):
        lines.extend(["", "Проверенного обучения пока нет."])
    lines.extend(["", "Рекомендации и INCONCLUSIVE не считаются проверенным обучением."])
    return "\n".join(lines)


def format_ads_history_screen(state: dict, *, title: str) -> str:
    lines = ["🕘 История", "", title]
    for item in state.get("history") or []:
        created = item.get("created_at")
        status = item.get("evaluation_status") or "PENDING"
        implementation = "IMPLEMENTED" if item.get("implementation_status") == "YES" else "НЕ ПОДТВЕРЖДЕНО"
        lines.extend(["", created.astimezone(MOSCOW).strftime("%d.%m") if created else "—",
                      _owner_short_text(item.get("title")), implementation, status])
        delta = item.get("delta") or {}
        cpa = delta.get("actual_cpa_7d") or {}
        if cpa.get("before") is not None and cpa.get("after") is not None:
            lines.append(f"CPA: {_ru_number(cpa['before'])} ₽ → {_ru_number(cpa['after'])} ₽")
    if not state.get("history"):
        lines.extend(["", "История появится после первой рекомендации."])
    return "\n".join(lines)


def format_ads_owner_main(*, campaign_name: str | None, checked_at, ai_status: str, summary: str, recommendation: str, confidence: str, health_status: str | None = None, decision_pack: dict | None = None, objective: dict | None = None) -> str:
    checked = checked_at.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M") if checked_at else "ещё не выполнялась"
    status = {
        "WAITING_FOR_CODEX": "Проверка ожидает анализа",
        "CODEX_PROCESSING": "AI анализирует данные",
        "CODEX_COMPLETED": "Анализ завершён",
        "CODEX_FAILED": "Анализ не завершён",
    }.get(ai_status, "Анализ ещё не выполнялся")
    warning = "\n\n⚠️ Данные неполные\nАнализ может быть менее точным." if health_status not in {None, "HEALTHY"} else ""
    pack = decision_pack or {}
    outcomes = pack.get("outcomes") or {}
    outcome_label = {"CHANNEL": "подписчик", "BOT": "запуск бота"}.get(pack.get("object_type"), "подтверждённый результат")
    outcome_value = outcomes.get("attributed_outcomes")
    outcome_line = f"\nПодтверждённые результаты ({outcome_label}): {outcome_value if outcome_value is not None else 'UNKNOWN'}" if pack else ""
    objective = objective or {}
    if objective.get("objective_mode") == "TARGET_CPA":
        target = objective.get("target_cpa")
        actual = objective.get("actual_cpa")
        state_label = {"ON_TARGET": "🟢 Цель выполняется", "OFF_TARGET": "🔴 Цель не выполняется", "INSUFFICIENT_DATA": "⚪ Пока недостаточно данных"}.get(objective.get("objective_state"), "⚪ Пока недостаточно данных")
        objective_block = f"\n\n🎯 Цель: ≤ {target:g} {objective.get('currency', 'RUB')}\n"
        objective_block += f"Сейчас: {actual:.2f} {objective.get('currency', 'RUB')}\n" if actual is not None else "Сейчас: UNKNOWN\n"
        objective_block += state_label
        if objective.get("absolute_gap") is not None:
            objective_block += f"\nРазрыв: {objective['absolute_gap']:+.2f} {objective.get('currency', 'RUB')}"
    else:
        objective_block = ""
    return (
        "🤖 AI Продвижение\n\n"
        f"Кампания: {campaign_name or 'не выбрана'}\n"
        f"Проверено: {checked}\n"
        f"Статус: {status}\n\n"
        f"Главный вывод:\n{_owner_short_text(summary, 'Данных для вывода пока нет.')}\n\n"
        f"Почему сейчас:\n{_owner_short_text(recommendation, 'Новых сигналов пока нет.')}\n\n"
        f"Уверенность: {_owner_confidence(confidence)}{outcome_line}{objective_block}"
        f"{warning}\n\n"
        "🔒 AI анализирует и советует, но ничего автоматически не меняет."
    )


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


def bot_landing_url(public_id: str) -> str:
    """Build a stable URL for a bot-scoped landing."""
    return public_landing_url(public_id)


def yandex_direct_oauth_url(admin_external_id: str, channel_id: str) -> str:
    """Build the channel-scoped OAuth entry point for Yandex Direct."""
    return (
        f"{settings.traffic_public_base_url.rstrip('/')}/oauth/yandex/direct/start"
        f"?admin_id={quote(admin_external_id, safe='')}&channel_id={quote(channel_id, safe='')}"
    )


def new_landing_public_id() -> str:
    """Return a short, public, numeric landing identifier.

    This identifier only addresses the landing. Per-visit attribution remains
    protected by the separately generated one-time startapp token.
    """
    return str(secrets.randbelow(90_000_000_000_000) + 10_000_000_000_000)


def promotion_channel_summary(channel_title: str, yandex_value: str | None,
                              counter_id: str | None, landing_url: str | None,
                              daily_reports_enabled: bool = True,
                              subscription_expires_at: datetime | None = None) -> str:
    """Render only channel-scoped, non-secret promotion readiness fields."""
    daily_reports_status = "✅ Включены" if daily_reports_enabled else "🔴 Отключены"
    return (
        f"📣 Продвижение\n\nКанал: {channel_title}\n"
        f"Ежедневные отчёты: {daily_reports_status}\n\n"
        f"Яндекс: {yandex_value or '-'}\n"
        f"Метрика: {counter_id or '-'}\n"
        f"Лендинг: {landing_url or '-'}"
    )


SUBSCRIPTION_PLANS = (
    ("1 месяц — 1 900 ₽", 1),
    ("3 месяца — 5 130 ₽ (−10%)", 3),
    ("6 месяцев — 7 980 ₽ (−30%)", 6),
)


def subscription_text(channel_title: str) -> str:
    return (
        f"💳 Оплата тарифа\n\n"
        f"📣 Канал: {channel_title}\n\n"
        "Выберите период подписки:"
    )


def service_subscription_text() -> str:
    return "💳 Оплата тарифа\n\n🤖 MAXIBOT\n\nПодписка не активна.\n\nВыберите период подписки:"


def subscription_quota(admin: Admin) -> tuple[int, int]:
    active = subscription_status(admin.subscription_expires_at) == "активен"
    tariff = (admin.subscription_tariff or "start").lower() if active else "free"
    if active and tariff == "free":
        tariff = "start"
    return {"free": (1, 0), "start": (3, 1), "pro": (10, 3)}.get(tariff, (1, 0))


def subscription_management_text(admin: Admin, *, channel_count: int = 0, bot_count: int = 0) -> str:
    active = subscription_status(admin.subscription_expires_at) == "активен"
    tariff = (admin.subscription_tariff or "start").lower() if active else "free"
    if active and tariff == "free":
        # Historical paid users predate the tariff field and map safely to Start.
        tariff = "start"
    tariff = tariff.title() if active else "Бесплатный"
    paid_until = (
        admin.subscription_expires_at.astimezone(MOSCOW).strftime("%d.%m.%Y")
        if active and admin.subscription_expires_at else "бессрочно"
    )
    return (
        "💳 Управление подпиской\n\n"
        f"Текущий тариф: {tariff}\n"
        f"Оплачено до: {paid_until}\n\n"
        f"Подключено каналов: {channel_count}/{subscription_quota(admin)[0]}\n"
        f"Подключено ботов: {bot_count}/{subscription_quota(admin)[1]}\n"
        "Ежедневные отчёты: ✅ Активно"
    )


def tariff_text(tariff: str) -> str:
    data = TARIFFS[tariff]
    monthly = data["monthly"]
    return (
        f"💳 Тариф {tariff.title()}\n\n"
        f"Каналы: до {data['channels']}\nБоты: до {data['bots']}\n"
        f"Цена: {monthly:g} ₽/мес.\n\nВыберите период оплаты:"
    )


def _remaining_access_text(expires_at: datetime, *, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    seconds = max(0, (expiry - current).total_seconds())
    if seconds < 24 * 60 * 60:
        return f"Осталось: {max(1, math.ceil(seconds / 3600))} часов"
    return f"Осталось: {math.ceil(seconds / (24 * 60 * 60))} дней"


def trial_subscription_status_text(trial_end_at: datetime, *, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    expiry = trial_end_at if trial_end_at.tzinfo else trial_end_at.replace(tzinfo=timezone.utc)
    return (
        "🎁 Пробный период активен\n\n"
        f"Активен до: {expiry.astimezone(MOSCOW):%d.%m.%Y}\n"
        f"{_remaining_access_text(expiry, now=current)}"
    )


def paid_subscription_status_text(subscription_expires_at: datetime, *, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    expiry = subscription_expires_at if subscription_expires_at.tzinfo else subscription_expires_at.replace(tzinfo=timezone.utc)
    return (
        "✅ Подписка активна\n\n"
        f"Оплачено до: {expiry.astimezone(MOSCOW):%d.%m.%Y}\n"
        f"{_remaining_access_text(expiry, now=current)}"
    )


def expired_trial_status_text() -> str:
    return "⏳ Бесплатный период завершён\n\nВыберите тариф, чтобы продолжить пользоваться MAXIBOT."


FREE_TRIAL_INFO_TEXT = (
    "🎁 7 дней бесплатно\n\n"
    "Попробуйте MAXIBOT бесплатно в течение 7 дней.\n\n"
    "✅ Один канал\n"
    "✅ Все функции продвижения\n"
    "✅ Интеграция с Яндекс.Директ\n"
    "✅ Аналитика и ежедневные отчёты\n\n"
    "Бесплатный период доступен один раз."
)


def free_trial_active_text(channel_title: str, trial_end_at: datetime) -> str:
    return f"🎁 Бесплатный период уже активен\n\nКанал: {channel_title}\nАктивен до: {trial_end_at.astimezone(MOSCOW):%d.%m.%Y}"


def free_trial_used_text() -> str:
    return "🎁 Бесплатный период уже использован.\n\nВыберите подходящий тариф, чтобы продолжить пользоваться MAXIBOT."


def promotion_yandex_summary(channel_title: str, connected: bool,
                             counter_id: str | None, conversions: int) -> str:
    return (
        f"⚡ Яндекс\n\n📣 {channel_title}\n\n"
        f"Статус: {'подключено' if connected else 'не подключено'}\n"
        f"Метрика: {counter_id or '—'}\n"
        f"Конверсии: {conversions}"
    )


def yandex_connection_instructions() -> str:
    """Canonical explanation shown above the Yandex authorization button."""
    return (
        "*Подключение Яндекс.Директ*\n\n"
        "Для настройки рекламных кампаний необходимо авторизоваться в Яндекс.\n\n"
        "*Инструкция:*\n"
        "1. Нажмите кнопку «Авторизоваться в Яндекс»\n"
        "2. Войдите в аккаунт Яндекс\n"
        "3. Разрешите доступ приложению\n"
        "4. Вы будете автоматически возвращены в бота"
    )


@dataclass(frozen=True)
class BotPromotionTarget:
    """Canonical promotion view for every BOT, including the system BOT.

    Storage remains compatibility-preserving: the system profile and customer
    BotConnection are adapters into this one presentation/domain contract.
    CHANNEL promotion is intentionally not represented here.
    """

    key: str
    title: str
    yandex_value: str | None
    counter_id: str | None
    landing_url: str | None
    yandex_action: str
    analytics_action: str
    ai_action: str


async def _bot_promotion_target(db: AsyncSession, admin: Admin, bot_key: str) -> BotPromotionTarget | None:
    """Resolve one owner-scoped BOT into the shared promotion contract."""
    if bot_key == "system":
        if not is_maxibot_owner(admin):
            return None
        profile = await db.scalar(select(BotPromotionProfile).where(
            BotPromotionProfile.bot_key == "maxibot",
            BotPromotionProfile.bot_type == "SYSTEM",
        ))
        if not profile:
            return BotPromotionTarget(
                key="system", title="MAXIBOT", yandex_value=None, counter_id=None,
                landing_url=None, yandex_action="promotion_maxibot_yandex",
                analytics_action="promotion_maxibot_analytics", ai_action="ads_bot:system",
            )
        connected = await system_connection_status(db=db)
        return BotPromotionTarget(
            key="system",
            title="MAXIBOT",
            yandex_value=profile.yandex_owner_login if profile.yandex_owner_login else ("Подключён" if connected else None),
            counter_id=profile.yandex_counter_id,
            landing_url=profile.landing_url,
            yandex_action="promotion_maxibot_yandex",
            analytics_action="promotion_maxibot_analytics",
            ai_action="ads_bot:system",
        )

    bot = await db.scalar(select(BotConnection).where(
        BotConnection.id == bot_key,
        BotConnection.owner_admin_id == admin.id,
    ))
    if not bot:
        return None
    profile = await db.scalar(select(BotPromotionProfile).where(
        BotPromotionProfile.bot_connection_id == bot.id,
        BotPromotionProfile.owner_admin_id == admin.id,
    ))
    yandex_value = None
    if profile and profile.yandex_owner_login:
        yandex_value = profile.yandex_owner_login
    elif profile and profile.yandex_oauth_token_encrypted:
        try:
            decrypt_token(profile.yandex_oauth_token_encrypted)
            yandex_value = "Подключён"
        except Exception:
            yandex_value = None
    return BotPromotionTarget(
        key=bot.id,
        title=bot.display_name or (f"@{bot.username.lstrip('@')}" if bot.username else "Бот"),
        yandex_value=yandex_value,
        counter_id=profile.yandex_counter_id if profile else None,
        landing_url=profile.landing_url if profile else None,
        yandex_action=f"promotion_bot_yandex:{bot.id}",
        analytics_action=f"promotion_bot_analytics:{bot.id}",
        ai_action=f"ads_bot:{bot.id}",
    )


def render_bot_promotion(target: BotPromotionTarget) -> tuple[str, list[list[dict]]]:
    """Single-source BOT promotion template for SYSTEM and CUSTOMER bots."""
    return (
        f"📣 Продвижение\n\nБот: {target.title}\n\n"
        f"Яндекс.Директ: {target.yandex_value or '—'}\n"
        f"Метрика: {target.counter_id or '—'}\n"
        f"Лендинг: {target.landing_url or '—'}",
        buttons(
            ("🔗 Яндекс.Директ ✅" if target.yandex_value else "🔗 Яндекс.Директ ❌", target.yandex_action),
            ("📊 Аналитика", target.analytics_action),
            ("🤖 AI Продвижение", target.ai_action),
            ("⬅️ Назад", "promotion"),
        ),
    )


async def traffic_stats(db: AsyncSession, channel_id: str) -> dict[str, int | float]:
    return await traffic_stats_for_channels(db, [channel_id])


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
            ("📚 Мои каналы", "promotion"),
            ("💳 Моя подписка", "subscription"),
            ("ℹ️ О сервисе", "about"),
        )
    if screen == "ads_storage":
        if not is_maxibot_owner(admin):
            return "⛔ Раздел доступен только владельцу MAXIBOT.", buttons(("⬅️ Главное меню", "home"))
        try:
            storage = await storage_diagnostics(db)
        except Exception:
            return "💾 Хранилище ADS\n\n⚠️ Данные хранилища временно недоступны.", buttons(("⬅️ Назад", "owner_admin"))
        periodic = storage["periodic"]
        status = {"NORMAL": "🟢 Норма", "WARNING": "🟡 Внимание", "CRITICAL": "🔴 Критично", "UNKNOWN": "⚠️ Недоступно"}.get(storage["status"], "⚠️ Недоступно")
        table_rows = []
        for table in storage["table_details"].values():
            latest = ui_time(table["latest"]) if table.get("latest") else "—"
            table_rows.append(
                f"• {table['name']}: {human_size(table['size'])}; "
                f"строк {table['row_count']}; latest {latest}; "
                f"retention {table['retention_class']}; {table['status']}"
            )
        rows = "\n".join(table_rows)
        last = ui_time(periodic.get("last_successful_collection"))
        next_due = ui_time(periodic.get("next_due_collection"))
        return (
            f"💾 Хранилище ADS\n\n{status}\n\n"
            f"PostgreSQL: {human_size(storage['database']['total'])}\n"
            f"ADS/V2: {human_size(storage['ads_total'])}\n{rows}\n\n"
            f"VPS: всего {human_size(storage['disk']['total'])}, свободно {human_size(storage['disk']['free'])} ({storage['disk']['free_percent']:.1f}%)\n\n"
            f"Рост 7 дней: {human_size(storage['growth_7d']) if storage.get('growth_7d') is not None else 'н/д'}\nРост 30 дней: {human_size(storage['growth_30d']) if storage.get('growth_30d') is not None else 'н/д'}\nПрогноз: {storage.get('growth_basis', 'недостаточно данных')}\n\n"
            f"Сбор данных: {'✅ включён' if periodic.get('enabled') else '⏸ выключен'}\n"
            f"Кампаний в сборе: {periodic.get('eligible_campaigns', 0)}\n"
            f"Последний успешный сбор: {last}\nСледующий сбор: {next_due}\n"
            f"Задержка: {'нет' if not periodic.get('stale_campaigns', 0) else 'есть'}\nОшибки: {periodic.get('failed_runs', 0)}\n"
            f"Повторных попыток в очереди: {periodic.get('retry_backlog', 0)}\n"
            f"Кампаний с устаревшими данными: {periodic.get('stale_campaigns', 0)}\n\n"
            f"Наблюдения: {settings.ads_observations_retention_days} дней\nОперационные записи: {settings.ads_operational_retention_days} дней\n"
            f"Дневные агрегаты: долгосрочно\nAI history: долгосрочно\n"
            f"Самое старое наблюдение: {ui_time(storage['oldest'].get('ads_observation'))}\n"
            f"Самый старый агрегат: {ui_time(storage['oldest'].get('daily_aggregate'))}\n"
            f"Старый collection run: {ui_time(storage['oldest'].get('collection_run'))}\n"
            f"Старый report run: {ui_time(storage['oldest'].get('report_run'))}\n"
            f"Retention last run: {ui_time(storage['retention'].get('last_run'))}\n"
            f"Удалено строк: {storage['retention'].get('deleted_rows', 0)}\n"
            f"Пропущено строк: {storage['retention'].get('skipped_rows', 0)}\n"
            f"Защищённых строк: {storage['retention'].get('skipped_rows', 0)}\n"
            f"Архивация данных: не используется, действует bounded retention"
        ), buttons(
            ("📊 Все 16 таблиц", "ads_storage_tables"),
            ("🎨 Креативы", "ads_storage_creatives"),
            ("📡 Сбор данных", "ads_storage_collection"),
            ("🧹 Retention", "ads_storage_retention"),
            ("⚠️ Ошибки", "ads_storage_errors"),
            ("⬅️ Назад", "owner_admin"),
        )
    if screen.startswith("ads_storage_"):
        if not is_maxibot_owner(admin):
            return "⛔ Раздел доступен только владельцу MAXIBOT.", buttons(("⬅️ Главное меню", "home"))
        try:
            storage = await storage_diagnostics(db)
        except Exception:
            return "💾 Хранилище ADS\n\n⚠️ Данные хранилища временно недоступны.", buttons(("⬅️ Назад", "ads_storage"))
        if screen == "ads_storage_tables":
            groups = {"CORE": TABLES[:5], "METRICS": TABLES[5:9], "SERVICE": TABLES[9:10], "AI": TABLES[10:]}
            text = "📊 ADS · 16/16 таблиц\n\n" + "\n".join(
                f"{group}: " + ", ".join(storage["table_details"][name]["name"] for name in names)
                for group, names in groups.items()
            )
        elif screen == "ads_storage_creatives":
            creative = storage.get("creatives", {})
            mail = "настроено" if creative.get("mail_configured") else "не настроено; оригиналы не загружаются"
            text = ("🎨 Креативы\n\n"
                    f"Записей: {creative.get('records', 0)}\nУникальных SHA-256: {creative.get('unique_hashes', 0)}\n"
                    f"STORED: {creative.get('media_stored', 0)} · отсутствуют: {creative.get('media_missing', 0)} · FAILED: {creative.get('upload_failed', 0)}\n"
                    f"Размер: {human_size(creative.get('media_bytes', 0))}\nMail.ru Cloud: {mail}\n\n"
                    "Проверка storage выполняется только по bounded диагностике.")
        elif screen == "ads_storage_collection":
            periodic = storage["periodic"]
            text = ("📡 Сбор данных\n\n"
                    f"Статус: {'включён' if periodic.get('enabled') else 'выключен'}\n"
                    f"Кампаний: {periodic.get('eligible_campaigns', 0)}\n"
                    f"Последний успешный сбор: {ui_time(periodic.get('last_successful_collection'))}\n"
                    f"Следующий сбор: {ui_time(periodic.get('next_due_collection'))}\n"
                    f"Ошибок: {periodic.get('failed_runs', 0)}")
        elif screen == "ads_storage_retention":
            retention = storage["retention"]
            text = ("🧹 Retention\n\n"
                    f"Наблюдения: {retention['observations_days']} дней\n"
                    f"Операционные записи: {retention['operational_days']} дней\n"
                    "Дневные агрегаты и AI history: долгосрочно\n"
                    f"Последний запуск: {ui_time(retention.get('last_run'))}")
        else:
            periodic = storage["periodic"]
            reason = "Нет критичных ошибок" if not periodic.get("failed_runs") else "Есть ошибки collection run; см. статус последнего запуска"
            text = f"⚠️ Ошибки ADS\n\n{reason}\nОшибок: {periodic.get('failed_runs', 0)}\nПовторных попыток: {periodic.get('retry_backlog', 0)}"
        return text, buttons(("⬅️ Хранилище ADS", "ads_storage"), ("⬅️ Администратор", "owner_admin"))
    if screen == "onboarding":
        return (
            "🤖 MAXIBOT\n\n"
            "Умный сервис для управления каналами в MAX\n\n"
            "👋 Добро пожаловать!\n\n"
            "MAXIBOT помогает владельцам каналов MAX:\n"
            "• подключать каналы и видеть статистику;\n"
            "• создавать лендинги для привлечения подписчиков;\n"
            "• отслеживать эффективность рекламы и подписки.\n\n"
            "Начните с подключения первого канала:",
            buttons(("📢 Подключить канал", "connect_channel"), ("💎 О сервисе", "about")),
        )
    if screen == "connect_channel":
        return channel_connection_instructions(), buttons(("⬅️ Главное меню", "home"))
    if screen == "promotion":
        if not channels and not is_maxibot_owner(admin):
            return channel_connection_instructions(), buttons(("⬅️ Назад", "home"))
        rows = [[(channel.title, f"promotion_channel:{channel.id}")] for channel in channels]
        if is_maxibot_owner(admin):
            rows.append([("🤖 MAXIBOT", "promotion_maxibot")])
        bot_rows = (await db.scalars(
            select(BotConnection)
            .where(BotConnection.owner_admin_id == admin.id)
            .order_by(BotConnection.display_name)
        )).all()
        rows.extend([
            [(f"🤖 {bot.display_name or bot.username}", f"promotion_bot:{bot.id}")]
            for bot in bot_rows
        ])
        rows.extend([
            [("📣 Подключить ещё канал", "promotion_connect")],
            [("🤖 Добавить бота", "connect_bot")],
            [("⬅️ Главное меню", "home")],
        ])
        return promotion_intro_text(), buttons(*rows)
    if screen == "promotion_connect":
        return promotion_connect_text(), buttons(("⬅️ Назад", "promotion"))
    if screen == "subscription":
        channel_count = await db.scalar(select(func.count(Channel.id)).where(Channel.admin_id == admin.id)) or 0
        bot_count = await db.scalar(select(func.count(BotConnection.id)).where(
            BotConnection.owner_admin_id == admin.id,
            BotConnection.status != "DISABLED",
        )) or 0
        return subscription_management_text(admin, channel_count=channel_count, bot_count=bot_count), buttons(
            ("Тариф Start", "subscription_tariff:start"),
            ("Тариф Pro", "subscription_tariff:pro"),
            ("🤖 AI Продвижение", "subscription_ai"),
            ("⬅️ Назад", "home"),
        )
    if screen.startswith("subscription_limit:"):
        kind = screen.split(":", 1)[1]
        resource = "каналов" if kind == "channel" else "ботов"
        return (
            f"🔒 Лимит тарифа\n\nВы достигли лимита подключённых {resource}.\n"
            "Выберите более высокий тариф, чтобы подключить ещё.",
            buttons(("💳 Управление подпиской", "subscription"), ("⬅️ Назад", "home")),
        )
    if screen.startswith("subscription_tariff:"):
        tariff = screen.split(":", 1)[1].lower()
        if tariff not in TARIFFS:
            return await render_screen(db, admin, "subscription")
        plan_buttons = [
            (
                f"{months} мес. — {amount_for_months(months, tariff):g} ₽"
                f" (−{int(DISCOUNTS[months] * 100)}%)" if DISCOUNTS[months] else
                f"{months} мес. — {amount_for_months(months, tariff):g} ₽",
                f"subscription_plan:service:{tariff}:{months}",
            )
            for months in (1, 3, 6)
        ]
        return tariff_text(tariff), buttons(*plan_buttons, ("⬅️ Назад", "subscription"))
    if screen == "subscription_ai":
        ai_active = subscription_status(admin.ai_promotion_expires_at) == "активен"
        ai_status = (
            f"AI Продвижение: активно\nОплачено до: {admin.ai_promotion_expires_at.astimezone(MOSCOW):%d.%m.%Y}"
            if ai_active and admin.ai_promotion_expires_at else "AI Продвижение: не подключено"
        )
        return (
            "🤖 AI Продвижение\n\n"
            "Анализирует продвижение, помогает оценивать цену подписчика, "
            "кампании и креативы и готовит рекомендации.\n\n"
            "Реклама автоматически не изменяется.\n\n"
            f"Стоимость: 1 ₽ в месяц.\n\n{ai_status}",
            buttons(("🤖 Подключить AI Продвижение — 1 ₽ в месяц", "subscription_ai_pay"), ("⬅️ Назад", "subscription")),
        )
    if screen == "free_trial":
        return FREE_TRIAL_INFO_TEXT, buttons(("🚀 Начать бесплатно", "free_trial_start"), ("⬅️ Назад", "subscription"))
    if screen == "free_trial_start":
        trial = await db.scalar(select(FreeTrial).where(FreeTrial.admin_id == admin.id))
        if trial:
            status = free_trial_status(trial.trial_end_at)
            if status == "активен":
                channel = await db.get(Channel, trial.activated_channel_id) if trial.activated_channel_id else None
                return free_trial_active_text(channel.title if channel else "Канал удалён", trial.trial_end_at), buttons(("⬅️ Назад", "subscription"))
            return free_trial_used_text(), buttons(("💳 Выбрать тариф", "subscription"), ("⬅️ Назад", "subscription"))
        if not channels:
            return (
                "🎁 Начало бесплатного периода\n\n"
                "Сначала подключите один канал. После успешного подключения бесплатный период будет активирован автоматически.",
                buttons(("📢 Подключить канал", "free_trial_connect"), ("⬅️ Назад", "subscription")),
            )
        if len(channels) == 1:
            return (
                f"🎁 Выберите канал для бесплатного периода\n\nКанал: {channels[0].title}",
                buttons(("🚀 Начать для этого канала", f"free_trial_activate:{channels[0].id}"), ("⬅️ Назад", "subscription")),
            )
        rows = [[(f"📣 {channel.title}", f"free_trial_activate:{channel.id}")] for channel in channels]
        rows.append([{"text": "⬅️ Назад", "callback_data": "subscription"}])
        return "🎁 Выберите один канал для бесплатного периода", rows
    if screen in {"promotion_maxibot", "promotion_bot:system"}:
        target = await _bot_promotion_target(db, admin, "system")
        return render_bot_promotion(target) if target else await render_screen(db, admin, "promotion")
    if screen in {"promotion_maxibot_landing", "promotion_maxibot_yandex", "promotion_maxibot_analytics", "promotion_maxibot_settings"}:
        if not is_maxibot_owner(admin):
            return await render_screen(db, admin, "promotion")
        profile = await db.scalar(select(BotPromotionProfile).where(
            BotPromotionProfile.bot_key == "maxibot",
            BotPromotionProfile.bot_type == "SYSTEM",
        ))
        if screen == "promotion_maxibot_landing":
            detail = f"🔗 {profile.landing_url}" if profile and profile.landing_url else "⛔ Системный landing ещё не настроен."
            body = f"📄 Лендинг\n\n🤖 MAXIBOT\n\n{detail}\n\nАтрибуция обычных каналов сюда не переносится."
        elif screen == "promotion_maxibot_yandex":
            counter = profile.yandex_counter_id if profile and profile.yandex_counter_id else "не настроен"
            goal = profile.yandex_goal_name if profile and profile.yandex_goal_name else "не настроена"
            status = await system_connection_status(db=db)
            body = f"🔗 Яндекс.Директ\n\n🤖 MAXIBOT\n\nСтатус: {'подключено' if status else 'не подключено'}\nСистемный счётчик: {counter}\nЦель: {goal}\n\nИспользуется только отдельная system-scoped конфигурация."
            if configured():
                system_oauth_url = f"{settings.traffic_public_base_url.rstrip('/')}/oauth/yandex/system/start?admin_id={quote(str(admin.external_id), safe='')}"
                oauth_label = "🔄 Переподключить Яндекс" if status else "🔐 Подключить Яндекс"
                return (body, [[{"text": oauth_label, "type": "link", "url": system_oauth_url}], [{"text": "⬅️ Назад", "callback_data": "promotion_maxibot"}]])
        elif screen == "promotion_maxibot_analytics":
            body = "📊 Аналитика\n\n🤖 MAXIBOT\n\nСистемный tracking ещё не активирован. Данные обычных каналов здесь не показываются."
        else:
            target = profile.target_url if profile and profile.target_url else MAXIBOT_CHANNEL_URL
            enabled = "включена" if profile and profile.enabled else "не включена"
            body = f"⚙️ Настройки продвижения\n\n🤖 MAXIBOT\n\nTarget: {target}\nСистемная конфигурация: {enabled}\n\nОбычные Channel/TrafficProfile не используются."
        return (body, buttons(("⬅️ MAXIBOT", "promotion_maxibot"), ("⬅️ Продвижение", "promotion")))
    if screen.startswith("promotion_bot:"):
        bot_id = screen.split(":", 1)[1]
        target = await _bot_promotion_target(db, admin, bot_id)
        return render_bot_promotion(target) if target else await render_screen(db, admin, "promotion")
    if screen.startswith(("promotion_bot_landing:", "promotion_bot_yandex:", "promotion_bot_analytics:", "promotion_bot_settings:")):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(
            BotConnection.id == bot_id,
            BotConnection.owner_admin_id == admin.id,
        ))
        if not bot:
            return await render_screen(db, admin, "promotion")
        if screen.startswith("promotion_bot_analytics:"):
            channel_ids = list((await db.scalars(select(Channel.id).where(
                Channel.admin_id == admin.id,
                Channel.bot_connection_id == bot.id,
            ))).all())
            stats = await traffic_stats_for_channels(db, channel_ids)
            title = bot.display_name or (f"@{bot.username.lstrip('@')}" if bot.username else "Бот")
            return (f"📊 Аналитика\n\n🤖 {title}\n\n"
                    f"👀 Посещений лендинга: {stats['visits']}\n"
                    f"📢 Перешли в MAX: {stats['to_channel']}\n👥 Всего новых участников: {stats['new_members']}\n"
                    f"↩️ Отписок: {stats['unsubscribed']}\n\nMAX signals:\n"
                    f"MAX_OPEN_LIKELY: {stats['max_open_likely']}\nMAX_OPEN_CONFIRMED: {stats['max_open_confirmed']}\n"
                    f"JOIN_REQUEST: {stats['join_events']}\n\nАтрибуция:\n"
                    f"EXACT: {stats['EXACT']}\nHIGH: {stats['HIGH']}\n"
                    f"MEDIUM: {stats['MEDIUM']}\nAMBIGUOUS: {stats['AMBIGUOUS']}\nUNATTRIBUTED: {stats['UNATTRIBUTED']}\n\n"
                    "В Яндекс отправляются только EXACT/HIGH; отправка по умолчанию в dry-run.",
                    buttons(("⬅️ Бот", f"promotion_bot:{bot.id}"), ("⬅️ Продвижение", "promotion")))
        labels = {
            "promotion_bot_landing": ("📄 Лендинг", "Лендинг для этого бота ещё не настроен."),
            "promotion_bot_yandex": ("", yandex_connection_instructions()),
            "promotion_bot_analytics": ("📊 Аналитика", "Системные и канальные данные здесь не показываются. Аналитика этого бота ещё не активирована."),
            "promotion_bot_settings": ("⚙️ Настройки", "Настройки продвижения этого бота ещё не созданы."),
        }
        title, detail = labels[screen.split(":", 1)[0]]
        return (f"{title}\n\n🤖 {bot.display_name or bot.username}\n\n{detail}",
                buttons(("⬅️ Бот", f"promotion_bot:{bot.id}"), ("⬅️ Продвижение", "promotion")))
    if screen.startswith((
        "promotion_channel:", "promotion_landing:", "promotion_analytics:",
        "promotion_yandex:", "promotion_settings:", "promotion_target_prompt:",
        "ads:", "ads_",
    )):
        selected_id = screen.split(":", 1)[1] if ":" in screen else None
        if selected_id and await resolve_channel_promotion_entitlement(db, admin.external_id, selected_id) == "NONE":
            return await render_screen(db, admin, f"subscription:{selected_id}", selected_id)
    if screen.startswith("promotion_channel:"):
        screen_parts = screen.split(":")
        selected_id = screen_parts[1]
        try:
            offset = max(0, int(screen_parts[2])) if len(screen_parts) > 2 else 0
        except ValueError:
            offset = 0
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel:
            return await render_screen(db, admin, "promotion")
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id))
        landing = await db.scalar(select(LandingPage).where(
            LandingPage.channel_id == channel.id,
            LandingPage.is_active.is_(True),
        ))
        yandex = await connection_status(admin.external_id, channel.id, db=db)
        metrica = await channel_setup_status(admin.external_id, channel.id)
        if metrica.get("counter_id") and yandex:
            try:
                encrypted = await get_encrypted_token(admin.external_id, channel.id, db=db)
                if encrypted:
                    await ensure_channel_goals_for_existing_counter(
                        admin.external_id, channel.id, encrypted, int(metrica["counter_id"])
                    )
            except Exception:
                logger.info("METRICA_GOAL_BACKFILL_DEFERRED channel_id=%s", channel.id)
        yandex_value = metrica.get("owner_login") or ("Подключён" if yandex else None)
        counter_id = (profile.yandex_counter_id if profile else None) or metrica.get("counter_id")
        yandex_button = "🔗 Яндекс.Директ ✅" if yandex else "🔗 Яндекс.Директ ❌"
        controls = [(yandex_button, f"promotion_yandex:{channel.id}")]
        if can_access_ads(admin.external_id):
            controls.append(("🤖 AI Продвижение", f"ads:{channel.id}"))
        controls.extend([
            ("📊 Аналитика", f"promotion_analytics:{channel.id}"),
            (("⛔ Отключить ежедневные отчёты" if channel.daily_reports_enabled else "✅ Включить ежедневные отчёты"),
             f"daily_reports:{channel.id}:{'off' if channel.daily_reports_enabled else 'on'}"),
            ("📩 Обращения / Предложка", f"channel:{channel.id}"),
            ("🗑 Удалить канал", f"delete:{channel.id}"),
            ("⬅️ Назад", "promotion"),
        ])
        logger.warning(
            "ADS_MENU_TRACE screen=promotion_channel instance=%s host=%s pid=%s "
            "owner=%s ads_enabled=%s ads_admin_only=%s can_access_ads=%s buttons=%s",
            f"{socket.gethostname()}:{os.getpid()}",
            socket.gethostname(),
            os.getpid(),
            bool(settings.maxibot_owner_user_id and str(admin.external_id) == str(settings.maxibot_owner_user_id)),
            settings.ads_enabled,
            settings.ads_admin_only,
            can_access_ads(str(admin.external_id)),
            [label for label, _ in controls],
        )
        return (
            promotion_channel_summary(
                channel.title,
                yandex_value,
                counter_id,
                public_landing_url(landing.public_id) if landing else None,
                channel.daily_reports_enabled,
                admin.subscription_expires_at,
            ),
            buttons(*controls),
        )
    if screen.startswith("subscription:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel:
            return await render_screen(db, admin, "promotion")
        plan_buttons = [
            (label, f"subscription_plan:{channel.id}:{months}")
            for label, months in SUBSCRIPTION_PLANS
        ]
        return (
            subscription_text(channel.title),
            buttons(*plan_buttons, ("⬅️ Назад", f"promotion_channel:{channel.id}")),
        )
    if screen.startswith("subscription_payment:"):
        order_id = screen.split(":", 1)[1]
        order = await db.scalar(select(SubscriptionOrder).where(
            SubscriptionOrder.id == order_id,
            SubscriptionOrder.admin_id == admin.id,
            SubscriptionOrder.status == "pending",
        ))
        if not order:
            return await render_screen(db, admin, "promotion")
        channel = await db.get(Channel, order.channel_id) if order.channel_id else None
        if order.channel_id and not channel:
            return await render_screen(db, admin, "promotion")
        title = f"📣 Канал: {channel.title}" if channel else "🤖 MAXIBOT"
        description = (
            f"MAXIBOT — подписка на канал «{channel.title}», {order.months} мес."
            if channel else f"MAXIBOT — тариф на весь сервис, {order.months} мес."
        )
        url = payment_url(
            invoice_id=order.invoice_id,
            amount=order.amount,
            description=description,
        )
        return (
            f"💳 Оплата тарифа\n\n{title}\n"
            f"Период: {order.months} мес.\nСумма: {order.amount:.0f} ₽\n\n"
            "Нажмите кнопку для перехода к оплате.",
            [[{"text": "💳 Перейти к оплате", "type": "link", "url": url}],
             [{"text": "⬅️ Назад", "callback_data": f"subscription:{channel.id}" if channel else "subscription"}]],
        )
    if screen.startswith("ads_objective:"):
        parts = screen.split(":", 2)
        object_type, object_id = parts[1], parts[2] if len(parts) > 2 else ""
        if not can_access_ads(admin.external_id) or object_type not in {"CHANNEL", "BOT"}:
            return await render_screen(db, admin, "promotion")
        if object_type == "CHANNEL":
            channel = await db.scalar(select(Channel).where(Channel.id == object_id, Channel.admin_id == admin.id))
            if not channel:
                return await render_screen(db, admin, "promotion")
            back = f"ads:{channel.id}"
            title = "📢 " + channel.title
        else:
            profile = await db.scalar(select(BotPromotionProfile).where(BotPromotionProfile.id == object_id, BotPromotionProfile.bot_key == "maxibot", BotPromotionProfile.bot_type == "SYSTEM"))
            if not profile or not is_maxibot_owner(admin):
                return await render_screen(db, admin, "promotion")
            back = "ads_bot:system"
            title = "🤖 MAXIBOT"
        campaigns = await campaigns_for_promotion_object(db, object_type=object_type, object_id=object_id)
        if object_type == "CHANNEL":
            title = "📣 Канал: " + channel.title
        else:
            title = "🤖 Бот: MAXIBOT"
        lines = [
            "🎯 Цель продвижения",
            "",
            title,
            "",
            "Цель помогает AI сравнивать фактическую стоимость",
            "подписчика с вашим ориентиром и готовить более точные",
            "рекомендации по продвижению.",
        ]
        controls = []
        if len(campaigns) > 1:
            controls.append(("🎯 Установить цель для всех активных кампаний", f"ads_objective_edit:{object_type}:{object_id}:all"))
            controls.extend((f"🎯 Установить цель: {campaign.name[:34]}", f"ads_objective_edit:{object_type}:{object_id}:{campaign.id}") for campaign in campaigns)
        else:
            controls.append(("🎯 Установить цель", f"ads_objective_edit:{object_type}:{object_id}:all"))
        controls.append(("⬅️ Назад", back))
        return ("\n".join(lines), buttons(*controls))
    if screen.startswith("ads_objective_input:"):
        _, object_type, object_id = screen.split(":", 2)
        back = f"ads_objective:{object_type}:{object_id}"
        return ("🎯 Введите целевую стоимость в рублях одним сообщением.\n\nНапример: 20", buttons(("⬅️ Назад", back)))
    if screen.startswith(("ads_bot_campaigns:", "ads_bot_creatives:", "ads_bot_context:", "ads_bot_learning:", "ads_bot_history:")):
        if not screen.endswith(":system") or not is_maxibot_owner(admin) or not can_access_ads(admin.external_id):
            return await render_screen(db, admin, "promotion")
        profile = await db.scalar(select(BotPromotionProfile).where(
            BotPromotionProfile.bot_key == "maxibot", BotPromotionProfile.bot_type == "SYSTEM"
        ))
        if not profile:
            return await render_screen(db, admin, "promotion")
        state = await build_ads_dashboard(db, object_type="BOT", object_id=profile.id)
        title = f"🤖 {profile.display_name or profile.bot_key}"
        prefix = screen.split(":", 1)[0]
        formatter = {
            "ads_bot_campaigns": format_ads_campaigns_screen,
            "ads_bot_creatives": format_ads_creatives_screen,
            "ads_bot_context": format_ads_why_screen,
            "ads_bot_learning": format_ads_learning_screen,
            "ads_bot_history": format_ads_history_screen,
        }[prefix]
        return formatter(state, title=title), buttons(("⬅️ AI Продвижение", "ads_bot:system"))
    if screen.startswith("ads_bot:"):
        from app.ads.service import latest_bot_view
        bot_key = screen.split(":", 1)[1]
        if bot_key != "system" or not is_maxibot_owner(admin) or not can_access_ads(admin.external_id):
            return await render_screen(db, admin, "promotion")
        profile = await db.scalar(select(BotPromotionProfile).where(BotPromotionProfile.bot_key == "maxibot", BotPromotionProfile.bot_type == "SYSTEM"))
        if not profile:
            return await render_screen(db, admin, "promotion")
        dashboard = await build_ads_dashboard(db, object_type="BOT", object_id=profile.id)
        return (
            format_ads_unified_dashboard(dashboard, title=profile.display_name or profile.bot_key),
            ads_unified_buttons("ads_bot", "system", "promotion_maxibot", objective_id=profile.id),
        )
        view = await latest_bot_view(db, profile.id)
        health = view["health"]; ai = view["ai"]; rec = view["recommendation"]
        facts = view["facts"] or {}; campaign = facts.get("campaign") or {}; decision_pack = facts.get("DIRECTOLOGIST") or {}
        status_map = {"WAITING_FOR_CODEX": "⏳ Ожидает Codex Worker", "CODEX_PROCESSING": "⚙️ Codex анализирует рекламу", "CODEX_COMPLETED": "✅ Завершён", "CODEX_FAILED": "🔴 Не завершён"}
        ai_status = status_map.get(ai.status, "AI-анализ ещё не выполнялся.") if ai else "AI-анализ ещё не выполнялся."
        result = json.loads(ai.validated_output_json) if ai and ai.status == "CODEX_COMPLETED" and ai.validated_output_json else {}
        body = format_ads_owner_main(
            campaign_name=campaign.get("name"), checked_at=view["checked_at"], ai_status=ai.status if ai else "",
            summary=(result.get("summary") if result else None) or (rec.title if rec else "—"),
            recommendation=((result.get("recommendation") or {}).get("reason") if result else None) or (rec.rationale if rec else "—"),
            confidence=(result.get("confidence") if result else None) or (rec.confidence if rec else "—"),
            health_status=health.get("status"), decision_pack=decision_pack, objective=facts.get("OBJECTIVE_EVALUATION"),
        )
        return (body, buttons(
            ("🔄 Проверить рекламу", "ads_bot_check:system"),
            ("🎯 Цель", f"ads_objective:BOT:{profile.id}"),
            ("🎯 Кампания", "ads_bot_campaigns:system"),
            ("💡 Рекомендации", "ads_bot_recommendations:system"),
            ("🔎 Почему AI так решил", "ads_bot_context:system"),
            ("🕘 История рекомендаций", "ads_bot_history:system"),
            ("⬅️ Назад", "promotion_maxibot"),
        ))
    if screen.startswith("ads:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel or not can_access_ads(admin.external_id):
            return await render_screen(db, admin, "promotion")
        dashboard = await build_ads_dashboard(db, object_type="CHANNEL", object_id=channel.id)
        return (
            format_ads_unified_dashboard(dashboard, title=channel.title),
            ads_unified_buttons("ads", channel.id, f"promotion_channel:{channel.id}"),
        )
        view = await latest_view(db, channel.id)
        health = view["health"]; ai = view["ai"]; rec = view["recommendation"]
        # Dashboard readiness must reflect the existing secure OAuth
        # connection, not a prior ADS snapshot. Requiring DIRECT_SYNC here
        # incorrectly showed "not connected" until the first AI check.
        direct_connected = bool(await connection_status(admin.external_id, channel.id, db=db))
        facts = view["facts"] or {}
        campaign = facts.get("campaign") or {}
        fact_values = facts.get("facts") or {}
        confidence_axes = facts.get("confidence_axes") or {}
        decision_pack = facts.get("DIRECTOLOGIST") or {}
        status_map = {"WAITING_FOR_CODEX": "⏳ Ожидает Codex Worker", "CODEX_PROCESSING": "⚙️ Codex анализирует рекламу", "CODEX_COMPLETED": "✅ Завершён", "CODEX_FAILED": "🔴 Не завершён"}
        ai_status = status_map.get(ai.status, "AI-анализ ещё не выполнялся.") if ai else "AI-анализ ещё не выполнялся."
        if ai and ai.status == "CODEX_COMPLETED" and ai.validated_output_json:
            result = json.loads(ai.validated_output_json); diagnosis = normalize_ai_text(result.get("summary") or "—", fact_values); recommendation = normalize_ai_text((result.get("recommendation") or {}).get("reason") or "—", fact_values); confidence = result.get("confidence") or "—"
            headline = "🟢 Анализ завершён"
        elif ai and ai.status == "CODEX_FAILED":
            result = {}; diagnosis = "Ответ AI не прошёл проверку или worker недоступен."; recommendation = "Попробуйте запустить проверку снова."; confidence = "—"; headline = "🔴 AI-анализ не завершён"
        else:
            result = {}; diagnosis = "—"; recommendation = "—"; confidence = "—"; headline = "🤖 AI Продвижение"
        owner_summary = (result.get("summary") if result else None) or (rec.title if rec else diagnosis)
        owner_reason = ((result.get("recommendation") or {}).get("reason") if result else None) or (rec.rationale if rec else recommendation)
        owner_confidence = (result.get("confidence") if result else None) or (rec.confidence if rec else confidence)
        body = format_ads_owner_main(
            campaign_name=campaign.get("name"),
            checked_at=view["checked_at"],
            ai_status=ai.status if ai else "",
            summary=owner_summary,
            recommendation=owner_reason,
            confidence=owner_confidence,
            health_status=health.get("status"),
            decision_pack=decision_pack, objective=facts.get("OBJECTIVE_EVALUATION"),
        )
        return (body, buttons(
            ("🔄 Проверить рекламу", f"ads_check:{channel.id}"),
            ("🎯 Цель", f"ads_objective:CHANNEL:{channel.id}"),
            ("🎯 Кампания", f"ads_campaigns:{channel.id}"),
            ("💡 Рекомендации", f"ads_recommendations:{channel.id}"),
            ("🔎 Почему AI так решил", f"ads_context:{channel.id}"),
            ("🕘 История рекомендаций", f"ads_history:{channel.id}"),
            ("⬅️ Назад", f"promotion_channel:{channel.id}"),
        ))
    if screen.startswith("ads_campaigns:"):
        screen_parts = screen.split(":")
        selected_id = screen_parts[1]
        try:
            offset = max(0, int(screen_parts[2])) if len(screen_parts) > 2 else 0
        except ValueError:
            offset = 0
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel or not can_access_ads(admin.external_id):
            return await render_screen(db, admin, "promotion")
        state = await build_ads_dashboard(db, object_type="CHANNEL", object_id=channel.id)
        return format_ads_campaigns_screen(state, title="Канал: " + channel.title), buttons(("⬅️ AI Продвижение", f"ads:{channel.id}"))
        campaigns = await campaigns_for_channel(db, channel.id)
        view = await latest_view(db, channel.id)
        fact_values = (view["facts"] or {}).get("facts") or {}
        metric_labels = (("Показы", "impressions_7d"), ("Клики", "clicks_7d"), ("Расход", "cost_7d"), ("Конверсии", "conversions_7d"), ("CTR", "ctr_7d"), ("CPC", "cpc_7d"), ("CPA", "actual_cpa_7d"), ("CR", "conversion_rate_7d"))
        available_metrics = [f"{label}: {_metric(key, fact_values.get(key))}" for label, key in metric_labels if fact_values.get(key) is not None]
        metrics_text = "\n".join(available_metrics) if available_metrics else "Метрики пока недоступны в сохранённых данных."
        if not campaigns:
            return (f"🎯 Кампания\n\n📢 {channel.title}\n\n{metrics_text}\n\nКампании пока нет в сохранённых данных.", buttons(("⬅️ AI Продвижение", f"ads:{channel.id}")))
        page_size = 10
        page = campaigns[offset:offset + page_size]
        rows = [(f"{'✅' if linked else '◻️'} {campaign.name[:42]}", f"ads_campaign_select:{channel.id}:{campaign.id}") for campaign, linked in page]
        if offset:
            rows.append(("⬅️ Предыдущие", f"ads_campaigns:{channel.id}:{max(0, offset - page_size)}"))
        if offset + page_size < len(campaigns):
            rows.append(("Следующие ➡️", f"ads_campaigns:{channel.id}:{offset + page_size}"))
        rows.append(("⬅️ AI Продвижение", f"ads:{channel.id}"))
        return (f"🎯 Кампания\n\n📢 {channel.title}\n\n{metrics_text}\n\nВыберите кампанию: {offset + 1}–{min(offset + page_size, len(campaigns))} из {len(campaigns)}.\n\n🔒 Только просмотр: данные рекламы не изменяются.", buttons(*rows))
    if screen.startswith(("ads_creatives:", "ads_learning:")):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel or not can_access_ads(admin.external_id):
            return await render_screen(db, admin, "promotion")
        state = await build_ads_dashboard(db, object_type="CHANNEL", object_id=channel.id)
        formatter = format_ads_creatives_screen if screen.startswith("ads_creatives:") else format_ads_learning_screen
        return formatter(state, title="Канал: " + channel.title), buttons(("⬅️ AI Продвижение", f"ads:{channel.id}"))
    if screen.startswith(("ads_context:", "ads_history:")):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel or not can_access_ads(admin.external_id):
            return await render_screen(db, admin, "promotion")
        state = await build_ads_dashboard(db, object_type="CHANNEL", object_id=channel.id)
        formatter = format_ads_why_screen if screen.startswith("ads_context:") else format_ads_history_screen
        return formatter(state, title="Канал: " + channel.title), buttons(("⬅️ AI Продвижение", f"ads:{channel.id}"))
    if screen.startswith("ads_context:") or screen.startswith("ads_history:") or screen.startswith("ads_recommendations:") or (screen.startswith("ads_memory:") and not screen.startswith("ads_memory_detail:")) or screen.startswith("ads_health:"):
        selected_id = screen.split(":", 1)[1]
        channel = await db.scalar(select(Channel).where(Channel.id == selected_id, Channel.admin_id == admin.id))
        if not channel or not can_access_ads(admin.external_id):
            return await render_screen(db, admin, "promotion")
        view = await latest_view(db, channel.id)
        if screen.startswith("ads_memory:"):
            rows = view["memories"]
            detail = "\n".join(f"• {_owner_short_text(row.content)}" for row in rows) or "Пока нет сохранённых наблюдений."
            title = "🔎 Почему AI так решил"
        elif screen.startswith("ads_health:"):
            health = view["health"]
            health_label = {"HEALTHY": "Данные в норме", "PARTIAL": "Данные частично доступны", "WARNING": "Данные требуют внимания", "UNHEALTHY": "Данные неполные"}.get(health.get("status"), "Качество данных не определено")
            detail = f"{health_label}.\n\n" + "\n".join(f"• {name}: {value}" for name, value in health.get("checks", {}).items())
            title = "🔎 Почему AI так решил"
        elif screen.startswith("ads_context:"):
            ai = await db.scalar(select(AdsAiRun).where(AdsAiRun.channel_id == channel.id).order_by(AdsAiRun.created_at.desc()))
            raw = json.loads(ai.safe_context_json) if ai and ai.safe_context_json else {}
            facts = (view["facts"] or {}).get("facts") or {}
            result = json.loads(ai.validated_output_json) if ai and ai.validated_output_json else {}
            recommendation = result.get("recommendation") or {}
            health = view["health"] or {}
            health_label = {"HEALTHY": "Данные в норме", "PARTIAL": "Данные частично доступны", "WARNING": "Данные требуют внимания", "UNHEALTHY": "Данные неполные"}.get(health.get("status"), "Качество данных не определено")
            detail = (
                f"Что увидел AI:\n{_owner_short_text(result.get('summary') or 'Последний анализ не содержит итогового вывода.')}\n\n"
                f"Почему такой вывод:\n{_owner_short_text(recommendation.get('reason') or 'Недостаточно подтверждённых сигналов для объяснения.')}\n\n"
                f"Уверенность: {_owner_confidence(result.get('confidence'))}\n"
                f"Качество данных: {health_label}\n"
                f"AI учитывал предыдущие проверки: {len(raw.get('RETRIEVED_PRIVATE_MEMORIES', []))}"
            )
            if facts.get("missing_data") or raw.get("MISSING_DATA"):
                detail += "\n\n⚠️ Данные неполные — анализ может быть менее точным."
            title = "🔎 Почему AI так решил"
        elif screen.startswith("ads_history:"):
            rows = list((await db.scalars(select(AdsAiRun).where(AdsAiRun.channel_id == channel.id).order_by(AdsAiRun.created_at.desc()).limit(10))).all())
            recommendation_ids = [row.id for row in rows]
            recommendations = list((await db.scalars(select(AdsRecommendation).where(AdsRecommendation.ai_run_id.in_(recommendation_ids)))).all()) if recommendation_ids else []
            recommendation_by_run = {item.ai_run_id: item for item in recommendations}
            detail = "\n".join(
                f"• {row.created_at:%d.%m %H:%M} — {_owner_short_text(recommendation_by_run[row.id].title) if row.id in recommendation_by_run else 'Рекомендация ещё не сформирована' }"
                for row in rows
            ) or "Истории рекомендаций пока нет."
            outcomes = list((await db.scalars(select(AdsAiOutcome).join(AdsRecommendation, AdsAiOutcome.recommendation_id == AdsRecommendation.id).where(AdsRecommendation.ai_run_id.in_(recommendation_ids)))).all()) if recommendation_ids else []
            if outcomes:
                detail += "\n\nИзмерение результата:\n" + "\n".join(f"• {item.horizon}: {'завершено' if item.status == 'COMPLETED' else 'ещё ожидается'}" for item in outcomes)
            title = "🕘 История рекомендаций"
        else:
            rec = await db.scalar(select(AdsRecommendation).where(AdsRecommendation.channel_id == channel.id, AdsRecommendation.status == "OPEN").order_by(AdsRecommendation.created_at.desc()))
            detail = (f"Что рекомендуется:\n{_owner_short_text(rec.title)}\n\nПочему:\n{_owner_short_text(rec.rationale)}\n\nПриоритет: {rec.priority}\nУверенность: {_owner_confidence(rec.confidence)}\nСтатус сигнала: {rec.data_sufficiency}\n\n⚠️ Только рекомендация. Автоматические изменения отключены." if rec else "Сейчас нет рекомендаций, требующих действий.")
            title = "💡 Рекомендации"
        controls = [("⬅️ AI Продвижение", f"ads:{channel.id}")]
        return (f"{title}\n\n📢 {channel.title}\n\n{detail}", buttons(*controls))
    if screen.startswith("ads_memory_detail:"):
        _, _, channel_id_value, memory_id = screen.split(":", 3)
        channel = await db.scalar(select(Channel).where(Channel.id == channel_id_value, Channel.admin_id == admin.id))
        memory = await db.scalar(select(AdsMemory).where(AdsMemory.id == memory_id, AdsMemory.channel_id == channel_id_value))
        if not channel or not memory or not can_access_ads(admin.external_id):
            return await render_screen(db, admin, "promotion")
        return (f"🧠 Память AI\n\nID: {memory.id}\nСтатус: {memory.status}\nИсточник: {memory.source_type}\nУверенность: {memory.confidence}\n\n{memory.content}\n\nEvidence: {memory.evidence_ids_json}", buttons(("✅ Подтвердить", f"ads_memory_status:{channel.id}:{memory.id}:VALIDATED"), ("❌ Отклонить", f"ads_memory_status:{channel.id}:{memory.id}:REJECTED"), ("⬅️ Память", f"ads_memory:{channel.id}")))
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
        status_data = await connection_status(admin.external_id, selected_id, db=db) if oauth_configured else None
        metrica = await channel_setup_status(admin.external_id, selected_id)
        counter_id = (profile.yandex_counter_id if profile else None) or metrica.get("counter_id")
        conversions = int(await db.scalar(
            select(func.count(OfflineConversion.id))
            .join(TrafficMembership, OfflineConversion.membership_id == TrafficMembership.id)
            .where(TrafficMembership.channel_id == selected_id)
        ) or 0)
        # Keep the same explanatory copy for both first-time and already-connected
        # flows; connection status and metrics remain available via the button flow.
        text = yandex_connection_instructions()
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
            "💎 **О сервисе**\n\n"
            "🤖 **MAXIBOT** — умный сервис для продвижения и управления каналами в MAX.\n\n"
            "**Наша цель:** помочь владельцам каналов находить свою аудиторию и привлекать подписчиков эффективнее.\n\n"
            "🌐 **Наш сайт:** [maxibot.pro](https://maxibot.jobjin.ru)",
            [
                [{"text": "📣 Наш MAX-канал", "type": "link", "url": MAXIBOT_CHANNEL_URL}],
                [support_button()],
                [{"text": "⬅️ Главное меню", "callback_data": "home"}],
            ],
        )
    if screen == "owner_admin":
        stats = await owner_admin_stats(db)
        try:
            storage = await storage_diagnostics(db)
            status_icon = {"NORMAL": "🟢", "WARNING": "🟡", "CRITICAL": "🔴", "UNKNOWN": "⚠️"}.get(storage["status"], "⚠️")
            storage_line = f"💾 ADS: {human_size(storage['ads_total'])} · {status_icon} свободно {human_size(storage['disk']['free'])}"
        except Exception:
            storage_line = "💾 ADS: ⚠️ данные хранилища недоступны"
        return (
            "🔐 MAXIBOT · Администратор\n\n"
            f"👤 Клиентов всего: {stats['clients_total']}\n"
            f"🆕 Новых сегодня: {stats['clients_today']}\n\n"
            f"📋 Подключено каналов: {stats['channels_total']}\n"
            f"🤖 Подключено своих ботов: {stats['custom_bots_total']}\n\n"
            f"🎫 Открытых тикетов: {stats['open_tickets']}\n\n"
            f"{storage_line}",
            buttons(("💾 Хранилище ADS", "ads_storage"), ("⬅️ Назад", "home")),
        )
    if screen == "support_prompt":
        return (
            "💬 Поддержка MAXIBOT\n\n"
            "Опишите ваш вопрос одним сообщением.\n\n"
            "Можно отправить текст, фото, видео или файл.\n\n"
            "Мы получим обращение и ответим вам здесь.",
            buttons(("❌ Отмена", "support_cancel")),
        )
    if screen == "support_direct":
        return (
            "💬 Поддержка MAXIBOT\n\n"
            "Нажмите кнопку ниже, чтобы открыть личный диалог с поддержкой.",
            [[support_button()], [{"text": "⬅️ Назад", "callback_data": "about"}]],
        )
    if screen.startswith("support_existing:"):
        ticket_id = screen.split(":", 1)[1]
        ticket = await db.scalar(select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.max_user_id == admin.external_id))
        if not ticket:
            return "ℹ️ О MAXIBOT", [[support_button()], [{"text": "⬅️ Назад", "callback_data": "home"}]]
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
        next_button = ("📚 Мои каналы", "channels") if channels else ("📢 Подключить канал", "connect_channel")
        return f"✅ Канал «{title}» удалён из MAXIBOT.", buttons(next_button)
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
            ("⬅️ Назад", f"promotion_channel:{channel.id}"),
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
        # Backward-compatible screen alias: the old overview duplicated the
        # `my_bots` screen and should no longer be rendered.
        return await render_screen(db, admin, "my_bots")
    if screen == "my_bots":
        # The separate bot list is retired. Connected bots are shown only in
        # the unified Мои каналы screen.
        return await render_screen(db, admin, "promotion")
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
                buttons(("✅ Продолжить подключение", "custom_bot_token"), ("⬅️ Назад", "promotion")))
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
                buttons(("📢 Мои каналы", "promotion")))
    if screen.startswith("custom_bot_active:"):
        bot_id = screen.split(":", 1)[1]
        bot = await db.scalar(select(BotConnection).where(BotConnection.id == bot_id, BotConnection.owner_admin_id == admin.id))
        if not bot: return "Бот не найден.", buttons(("⬅️ Назад", "my_bots"))
        return (f"✅ Бот подключён\n\n🤖 {bot.display_name}\n@{bot.username}\n\nВаш бот готов к работе.\n\n🔗 Ссылка для пользователей:\nhttps://max.ru/{bot.username.lstrip('@')}", buttons(("📢 Мои каналы", "promotion")))
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
        return (f"🤖 Управление ботом\n\n{bot.display_name}\n@{username}\n\n🟢 Статус: Работает", buttons(("⬅️ Мои боты", "my_bots")))
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
    async def bind_system_startapp(self, token: str, max_user_id: str) -> bool:
        if not max_user_id:
            return False
        async with self.session_factory() as db:
            visit = await db.scalar(select(BotTrafficVisit).where(BotTrafficVisit.token == token)) if token else None
            if not visit and not token:
                # A real bot_started update can arrive without the payload
                # after Mini App resolve.  Recover only the same user's most
                # recent verified technical handoff.
                visit = await db.scalar(select(BotTrafficVisit).where(
                    BotTrafficVisit.max_user_id == str(max_user_id),
                    BotTrafficVisit.bot_id == settings.max_bot_id,
                    BotTrafficVisit.destination_id == settings.max_bot_id,
                    BotTrafficVisit.status.in_(("HANDOFF_CONFIRMED", "BOT_STARTED")),
                    BotTrafficVisit.created_at >= datetime.now(timezone.utc) - timedelta(hours=settings.traffic_attribution_window_hours),
                ).order_by(BotTrafficVisit.created_at.desc()))
            if not visit or visit.bot_id != settings.max_bot_id or visit.destination_id != settings.max_bot_id:
                return False
            # Mini App resolve must already have confirmed this exact MAX
            # identity on this exact original system visit.
            identity_link = await db.scalar(select(BotMaxIdentityLink).where(
                BotMaxIdentityLink.bot_visit_id == visit.id,
                BotMaxIdentityLink.bot_id == settings.max_bot_id,
                BotMaxIdentityLink.destination_id == settings.max_bot_id,
                BotMaxIdentityLink.max_user_id == str(max_user_id),
                BotMaxIdentityLink.startapp_token == visit.token,
            ))
            if not identity_link or not visit.max_user_id or visit.max_user_id != str(max_user_id):
                return False
            if visit.status not in {"HANDOFF_CONFIRMED", "BOT_STARTED"}:
                return False
            started_at = visit.started_at or datetime.now(timezone.utc)
            handoff_event = await db.scalar(select(BotTrafficEvent).where(
                BotTrafficEvent.bot_visit_id == visit.id,
                BotTrafficEvent.event_type == "BOT_HANDOFF_CONFIRMED",
            ))
            timeout_reached = start_wait_timeout_reached(
                handoff_event.event_time if handoff_event else None,
                now=started_at,
                wait_seconds=settings.bot_start_wait_seconds,
            )
            if not await db.scalar(select(BotTrafficEvent.id).where(
                BotTrafficEvent.bot_visit_id == visit.id,
                BotTrafficEvent.event_type == "REAL_START_RECEIVED",
            )):
                db.add(BotTrafficEvent(
                    bot_visit_id=visit.id,
                    bot_id=visit.bot_id,
                    destination_id=visit.destination_id,
                    event_type="REAL_START_RECEIVED",
                    event_time=started_at,
                    metadata_json=json.dumps({
                        "real_start_received": True,
                        "start_wait_timeout_reached": "YES" if timeout_reached else "NO",
                        "dropoff_classification": dropoff_classification(
                            real_start_received=True,
                            timeout_reached=timeout_reached,
                        ),
                        "bot_start_wait_seconds": max(0, settings.bot_start_wait_seconds),
                    }, ensure_ascii=False),
                    dedupe_key=f"bot-telemetry:{visit.id}:REAL_START_RECEIVED",
                ))
            visit.started_at = started_at
            # This method is called from the canonical MAX bot_started runtime
            # event, so this is the first point where the business goal may be
            # recorded.  Mini App resolve above remains telemetry-only.
            visit.status = "BOT_STARTED"
            paid_journey_key = paid_bot_conversion_key(
                bot_id=visit.bot_id,
                max_user_id=str(max_user_id),
                yclid=visit.yclid,
            )
            values = {
                "bot_visit_id": visit.id,
                "bot_id": visit.bot_id,
                "destination_id": visit.destination_id,
                "max_user_id": str(max_user_id),
                "internal_event": "bot_started",
                "status": "pending",
                "paid_journey_key": paid_journey_key,
            }
            if paid_journey_key:
                dialect = db.bind.dialect.name
                if dialect == "postgresql":
                    stmt = postgres_insert(BotConversion).values(**values).on_conflict_do_nothing()
                elif dialect == "sqlite":
                    stmt = sqlite_insert(BotConversion).values(**values).on_conflict_do_nothing()
                else:
                    stmt = insert(BotConversion).values(**values)
                await db.execute(stmt)
            else:
                db.add(BotConversion(**values))
            await db.commit()
            return True

    async def queue_system_channel_connected(self, max_user_id: str) -> str:
        """Queue a bot-branch conversion only after an official channel connect."""
        if not max_user_id:
            return "blocked_no_identity"
        async with self.session_factory() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.traffic_attribution_window_hours)
            visit = await db.scalar(select(BotTrafficVisit).where(
                BotTrafficVisit.max_user_id == str(max_user_id),
                BotTrafficVisit.bot_id == settings.max_bot_id,
                BotTrafficVisit.destination_id == settings.max_bot_id,
                BotTrafficVisit.status == "BOT_STARTED",
                BotTrafficVisit.created_at >= cutoff,
            ).order_by(BotTrafficVisit.created_at.desc()))
            if not visit:
                return "blocked_no_system_visit"
            exists_conversion = await db.scalar(select(BotConversion.id).where(
                BotConversion.bot_visit_id == visit.id,
                BotConversion.internal_event == "maxibot_channel_connected",
            ))
            if exists_conversion:
                return "already_queued"
            db.add(BotConversion(
                bot_visit_id=visit.id,
                bot_id=visit.bot_id,
                destination_id=visit.destination_id,
                max_user_id=str(max_user_id),
                internal_event="maxibot_channel_connected",
                status="pending",
            ))
            await db.commit()
            return "queued"
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def _subscription_limits(admin: Admin) -> tuple[int, int]:
        return subscription_quota(admin)

    async def _check_quota(self, db, admin: Admin, *, bots: bool = False) -> None:
        channels = await db.scalar(select(func.count(Channel.id)).where(Channel.admin_id == admin.id)) or 0
        bot_count = await db.scalar(select(func.count(BotConnection.id)).where(
            BotConnection.owner_admin_id == admin.id,
            BotConnection.status != "DISABLED",
        )) or 0
        max_channels, max_bots = self._subscription_limits(admin)
        if not bots and channels >= max_channels:
            raise ValueError("SUBSCRIPTION_CHANNEL_LIMIT")
        if bots and bot_count >= max_bots:
            raise ValueError("SUBSCRIPTION_BOT_LIMIT")

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
                await self._check_quota(db, admin, bots=True)
                bot = BotConnection(owner_admin_id=admin.id, owner_max_user_id=external_id,
                    max_bot_id=str(profile.get("user_id") or profile.get("id") or profile.get("bot_id") or "unknown"),
                    username=str(profile.get("username") or profile.get("name") or "bot"),
                    display_name=str(profile.get("name") or profile.get("display_name") or profile.get("username") or "MAX-бот"),
                    public_id=new_landing_public_id(),
                    encrypted_token=encrypted_token, status="WAITING_ADMIN_START")
                db.add(bot)
                await db.flush()
            promotion = await db.scalar(select(BotPromotionProfile).where(
                BotPromotionProfile.bot_connection_id == bot.id,
            ))
            if not promotion:
                db.add(BotPromotionProfile(
                    bot_key=bot.max_bot_id,
                    bot_type="CUSTOMER",
                    owner_admin_id=admin.id,
                    bot_connection_id=bot.id,
                    enabled=True,
                ))
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

    async def channel_promotion_entitlement(self, admin_external_id: str, channel_id: str) -> str:
        async with self.session_factory() as db:
            return await resolve_channel_promotion_entitlement(db, admin_external_id, channel_id)

    async def free_trial_state(self, admin_external_id: str) -> dict[str, object]:
        """Return service-level trial state and the one-channel quota marker."""
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == str(admin_external_id)))
            trial = await db.scalar(select(FreeTrial).where(FreeTrial.admin_id == admin.id)) if admin else None
            if not trial:
                return {"status": "AVAILABLE", "activated_channel_id": None, "trial_end_at": None}
            status = free_trial_status(trial.trial_end_at)
            return {
                "status": "ACTIVE" if status == "активен" else "EXPIRED",
                "activated_channel_id": trial.activated_channel_id,
                "trial_end_at": trial.trial_end_at,
            }

    async def ensure_existing_channel_free_trial(self, admin_external_id: str) -> dict[str, object]:
        """Lazily consume the one trial for an already connected first channel."""
        async with self.session_factory() as db:
            admin = await db.scalar(
                select(Admin).where(Admin.external_id == str(admin_external_id)).with_for_update()
            )
            if not admin or subscription_status(admin.subscription_expires_at) == "активен":
                return {"status": "PAID" if admin and subscription_status(admin.subscription_expires_at) == "активен" else "UNAVAILABLE"}
            trial = await db.scalar(select(FreeTrial).where(FreeTrial.admin_id == admin.id).with_for_update())
            if trial:
                status = free_trial_status(trial.trial_end_at)
                return {
                    "status": "ACTIVE" if status == "активен" else "EXPIRED",
                    "activated_channel_id": trial.activated_channel_id,
                    "trial_end_at": trial.trial_end_at,
                }
            channel = await db.scalar(
                select(Channel)
                .where(Channel.admin_id == admin.id)
                .order_by(Channel.connected_at.asc(), Channel.id.asc())
                .limit(1)
            )
            if not channel:
                return {"status": "AVAILABLE"}
            started = datetime.now(timezone.utc)
            trial = FreeTrial(
                admin_id=admin.id,
                activated_channel_id=channel.id,
                status="ACTIVE",
                trial_start_at=started,
                trial_end_at=started + timedelta(days=7),
            )
            db.add(trial)
            admin.pending_free_trial = False
            await db.commit()
            return {"status": "ACTIVE", "activated_channel_id": channel.id, "trial_end_at": trial.trial_end_at}

    async def begin_free_trial(self, admin_external_id: str) -> dict[str, object]:
        """Remember explicit intent until a channel is successfully connected."""
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == str(admin_external_id)))
            if not admin:
                return {"status": "UNAVAILABLE"}
            trial = await db.scalar(select(FreeTrial).where(FreeTrial.admin_id == admin.id))
            if trial:
                status = free_trial_status(trial.trial_end_at)
                return {"status": "ACTIVE" if status == "активен" else "EXPIRED"}
            admin.pending_free_trial = True
            await db.commit()
            return {"status": "PENDING"}

    async def cancel_free_trial_intent(self, admin_external_id: str) -> None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == str(admin_external_id)))
            if admin and admin.pending_free_trial:
                admin.pending_free_trial = False
                await db.commit()

    async def activate_free_trial(self, admin_external_id: str, channel_id: str) -> dict[str, object]:
        """Atomically grant the service entitlement and consume one channel quota."""
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == str(admin_external_id)).with_for_update())
            channel = await db.scalar(select(Channel).where(
                Channel.id == str(channel_id), Channel.admin_id == admin.id if admin else False,
            ))
            if not admin or not channel:
                return {"status": "NOT_ALLOWED"}
            trial = await db.scalar(select(FreeTrial).where(FreeTrial.admin_id == admin.id).with_for_update())
            if trial:
                status = free_trial_status(trial.trial_end_at)
                return {"status": "ACTIVE" if status == "активен" else "EXPIRED", "activated_channel_id": trial.activated_channel_id}
            started = datetime.now(timezone.utc)
            trial = FreeTrial(
                admin_id=admin.id,
                activated_channel_id=channel.id,
                status="ACTIVE",
                trial_start_at=started,
                trial_end_at=started + timedelta(days=7),
            )
            db.add(trial)
            admin.pending_free_trial = False
            await db.commit()
            return {"status": "ACTIVE", "activated_channel_id": channel.id, "trial_end_at": trial.trial_end_at}

    async def activate_pending_free_trial(self, admin_external_id: str, channel_id: str) -> dict[str, object] | None:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == str(admin_external_id)))
            pending = bool(admin and admin.pending_free_trial)
        if not pending:
            return None
        return await self.activate_free_trial(admin_external_id, channel_id)

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
                registry = await db.scalar(select(ChannelSlugRegistry).where(
                    ChannelSlugRegistry.max_channel_external_id == str(channel.external_id)
                ))
                if registry and landing and not registry.landing_public_id:
                    registry.landing_public_id = landing.public_id
                if not landing:
                    stable_public_id = registry.landing_public_id if registry and registry.landing_public_id else new_landing_public_id()
                    db.add(LandingPage(
                        channel_id=channel.id, public_id=stable_public_id,
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
                registry = None
            stable_public_id = registry.landing_public_id if registry and registry.landing_public_id else new_landing_public_id()
            if registry:
                registry.landing_public_id = stable_public_id
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
                channel_id=channel.id, public_id=stable_public_id, title=payload["title"],
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
            if await resolve_channel_promotion_entitlement(db, external_id, channel.id) == "NONE":
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
                queued_unsubscribe = None
                if membership.attributed_visit_id:
                    exact_visit = await db.get(TrafficVisit, membership.attributed_visit_id)
                    subscription = await db.scalar(select(TrafficSubscription).where(
                        TrafficSubscription.visitor_id == exact_visit.id,
                        TrafficSubscription.channel_id == channel.id,
                    ))
                    if subscription:
                        subscription.status = "lost"
                        subscription.lost_at = event_time
                    if (
                        membership.attribution_status == EXACT
                        and exact_visit
                        and exact_visit.yclid
                        and exact_visit.metrica_client_id
                    ):
                        exists_outbox = await db.scalar(select(OfflineConversion.id).where(
                            OfflineConversion.visit_id == exact_visit.id,
                            OfflineConversion.membership_id == membership.id,
                            OfflineConversion.target == "YANDEX",
                            OfflineConversion.goal == "userDidUnsubscribe",
                        ))
                        if not exists_outbox:
                            queued_unsubscribe = OfflineConversion(
                                visit_id=exact_visit.id, membership_id=membership.id,
                                target="YANDEX", goal="userDidUnsubscribe",
                                identifier_type="yclid", identifier_value=exact_visit.yclid,
                                conversion_datetime=event_time,
                                status="dry_run" if settings.yandex_conversion_dry_run else "pending",
                            )
                            db.add(queued_unsubscribe)
                    if exact_visit:
                        exact_visit.converted_at = exact_visit.converted_at or event_time
                await db.commit()
                if queued_unsubscribe:
                    await _send_exact_conversion(db, queued_unsubscribe, membership, channel)
                return True
            else:
                membership.current_member = True
                membership.joined_at = membership.joined_at or event_time
                # Replayed official events must not overwrite a previously
                # confirmed exact attribution after its intent was consumed.
                if event_type.lower() == "user_added" and membership.attribution_status == EXACT and membership.attributed_visit_id:
                    await db.commit()
                    return True
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
                exact_visit_id = None
                exact_reason = None
                if event_type.lower() == "user_added":
                    exact_visit_id = await consume_single_active_join_intent(
                        db, channel_id=channel.id, max_user_id=str(max_user_id), now=event_time,
                    )
                    if exact_visit_id:
                        exact_reason = "EXACT_VERIFIED_CHANNEL_OPEN_INTENT"
                if not exact_visit_id:
                    exact_visit_id = resolve_exact_identity_visit(identity_visits)
                    if exact_visit_id:
                        exact_reason = "EXACT_VERIFIED_STARTAPP_IDENTITY"
                if event_type.lower() == "user_added" and exact_visit_id:
                    membership.attribution_status = EXACT
                    membership.attribution_reason = exact_reason
                    membership.attributed_visit_id = exact_visit_id
                    membership.attribution_score = 100
                    visit = await db.get(TrafficVisit, exact_visit_id)
                    queued_conversion = None
                    if visit:
                        visit.converted_at = visit.converted_at or event_time
                        if can_send_offline_conversion(EXACT, visit.yclid):
                            exists_outbox = await db.scalar(select(OfflineConversion.id).where(
                                OfflineConversion.visit_id == visit.id,
                                OfflineConversion.membership_id == membership.id,
                                OfflineConversion.target == "YANDEX",
                                OfflineConversion.goal == "userDidSubscribe",
                            ))
                            if not exists_outbox:
                                queued_conversion = OfflineConversion(
                                    visit_id=visit.id, membership_id=membership.id,
                                    target="YANDEX", goal="userDidSubscribe",
                                    identifier_type="yclid" if visit.yclid else "client_id",
                                    identifier_value=visit.yclid or visit.metrica_client_id,
                                    conversion_datetime=event_time,
                                    status="dry_run" if settings.yandex_conversion_dry_run else "pending",
                                )
                                db.add(queued_conversion)
                    await db.commit()
                    if queued_conversion:
                        await _send_exact_conversion(db, queued_conversion, membership, channel)
                    return True
                # A verified startapp identity is deterministic evidence for
                # this visitor. Do not mix it with the old temporal-candidate
                # path used when MAX sends a bare user_added event.
                membership.attribution_reason = (
                    "NO_VERIFIED_STARTAPP_IDENTITY"
                    if not identity_visits
                    else "MULTIPLE_VERIFIED_IDENTITY_VISITS"
                )
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
                # Temporal matching is retained solely for diagnostics.  It
                # must never queue a Yandex conversion: only the exact branch
                # above has verified startapp -> MAX identity evidence.
                if decision.status == EXACT and decision.visit_id:
                    visit = await db.get(TrafficVisit, decision.visit_id)
                    if visit:
                        visit.converted_at = visit.converted_at or event_time
                        if can_send_offline_conversion(decision.status, visit.yclid):
                            exists_outbox = await db.scalar(select(OfflineConversion.id).where(
                                OfflineConversion.visit_id == visit.id,
                                OfflineConversion.membership_id == membership.id,
                                OfflineConversion.target == "YANDEX",
                                OfflineConversion.goal == "userDidSubscribe",
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

    async def is_platform_admin(self, external_id: str | None) -> bool:
        """Return true only for the configured MAXIBOT administrator.

        ``is_admin`` is intentionally a per-user workspace/owner lookup and
        is true for every user who has a workspace row.  The hidden /admin
        command must use the separate platform-owner allowlist instead.
        """
        configured_owner = (settings.maxibot_owner_user_id or "").strip()
        return bool(external_id and configured_owner and str(external_id) == configured_owner)

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
            if not visit or not visit.yclid:
                return False
            membership.attribution_status = EXACT
            membership.attribution_reason = "EXACT_VERIFIED_STARTAPP_IDENTITY"
            membership.attributed_visit_id = exact_visit_id
            membership.attribution_score = 100
            visit.converted_at = visit.converted_at or event_time
            exists_outbox = await db.scalar(select(OfflineConversion.id).where(
                OfflineConversion.visit_id == visit.id,
                OfflineConversion.membership_id == membership.id,
                OfflineConversion.target == "YANDEX",
                OfflineConversion.goal == "userDidSubscribe",
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
        if screen == "subscription":
            await self.ensure_existing_channel_free_trial(external_id)
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if not admin:
                admin = Admin(external_id=external_id)
                db.add(admin)
                await db.flush()
            text, keyboard = await render_screen(db, admin, screen, channel_id)
            # Keep the first-run screen text-only; established menu screens
            # remain unchanged as well.
            media = []
            return text, keyboard, admin.service_message_id, media

    async def onboarding_seen(self, external_id: str) -> bool:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            return bool(admin and admin.onboarding_seen)

    async def mark_onboarding_seen(self, external_id: str) -> bool:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if not admin:
                admin = Admin(external_id=external_id)
                db.add(admin)
            admin.onboarding_seen = True
            await db.commit()
            return True

    async def reset_onboarding(self, external_id: str) -> bool:
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == external_id))
            if not admin:
                return False
            admin.onboarding_seen = False
            await db.commit()
            return True

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

    async def set_daily_reports(self, channel_id: str, enabled: bool):
        async with self.session_factory() as db:
            row = await db.execute(select(Channel, Admin).join(Admin).where(Channel.id == channel_id))
            channel, admin = row.first() or (None, None)
            if channel and admin and await resolve_channel_promotion_entitlement(db, admin.external_id, channel.id) != "NONE":
                channel.daily_reports_enabled = enabled
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
                    landing_public_id=None,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(registry)
            landing = await db.scalar(select(LandingPage).where(
                LandingPage.channel_id == channel.id, LandingPage.is_active.is_(True),
            ))
            if landing and not registry.landing_public_id:
                registry.landing_public_id = landing.public_id
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
            await self._check_quota(db, admin)
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
