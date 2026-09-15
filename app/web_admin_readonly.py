"""Read-only platform-admin business overview API.

This module deliberately exposes aggregates and internal opaque ids only. It
does not expose MAX/Yandex credentials, tokens, raw attribution identities, or
mutation endpoints.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.ads.storage_health import diagnostics as ads_storage_diagnostics
from app.db.models import (
    Admin,
    AdsAiRun,
    AdsAccount,
    AdsCampaign,
    AdsCollectionRun,
    AdsDataHealth,
    TelegramAdsServiceIdentity,
    AdsMemory,
    AdsAnalysisSnapshot,
    BotConnection,
    BotConversion,
    BotMaxIdentityLink,
    BotPromotionProfile,
    BotTrafficEvent,
    BotTrafficVisit,
    Channel,
    Conversation,
    MaxIdentityLink,
    MaxJoinEvent,
    OfflineConversion,
    SubscriptionOrder,
    TrafficProfile,
    TrafficEvent,
    TrafficMembership,
    TrafficVisit,
    QaRun,
)
from app.db.session import get_session
from app.qa_observability import UNKNOWN, decode_json, parity_status, persist_qa_result
from app.web_admin_auth import require_edge_token, require_qa_ingest_token, require_web_admin

router = APIRouter(prefix="/admin", tags=["web-admin"])


CHANNEL_STAGES = (
    ("direct_in", "Direct", "UNKNOWN", "Внешний отчёт Яндекс.Директ"),
    ("landing", "Landing", "EXACT", "TrafficVisit"),
    ("traffic_visit", "TrafficVisit(channel_id)", "EXACT", "TrafficVisit(channel_id)"),
    ("token", "Token", "EXACT", "visit_token"),
    ("startapp", "Startapp", "EXACT", "startapp_token"),
    ("miniapp", "MAX Mini App", "EXACT", "MINI_APP_LOADED"),
    ("identity", "Verified MAX user", "EXACT", "verified initData"),
    ("resolve", "Resolve token", "EXACT", "RESOLVE_OK"),
    ("identity_link", "MaxIdentityLink", "EXACT", "MaxIdentityLink"),
    ("channel_validation", "Проверка channel_id", "DERIVED", "успешный resolve/channel guard"),
    ("channel_open", "Открытие конкретного канала", "ATTEMPT", "MINIAPP_CHANNEL_HANDOFF_STARTED; actual open UNKNOWN"),
    ("join_request", "Join request", "EXACT", "join_request_created/join_request"),
    ("approve", "Approve", "DERIVED", "user_added после ручного approve; прямой approve event может отсутствовать"),
    ("user_added", "user_added(user_id + channel_id)", "EXACT", "official MAX user_added"),
    ("revalidation", "Повторная проверка channel_id + MaxIdentityLink", "DERIVED", "exact attributed membership"),
    ("restore", "Восстановление TrafficVisit / yclid", "EXACT", "attributed_visit_id / EXACT attribution"),
    ("subscriber", "max_subscriber", "EXACT", "TrafficMembership exact conversion"),
    ("metrika", "Metrika", "EXACT", "OfflineConversion processed by Yandex"),
    ("direct_out", "Direct", "UNKNOWN", "внешняя reporting evidence с задержкой"),
)
BOT_STAGES = (
    ("direct_in", "Direct", "UNKNOWN", "Внешний отчёт Яндекс.Директ"),
    ("landing", "Landing", "EXACT", "BotTrafficVisit"),
    ("traffic_visit", "BotTrafficVisit(bot_id)", "EXACT", "BotTrafficVisit(bot_id)"),
    ("token", "Token", "EXACT", "opaque BotTrafficVisit.token"),
    ("startapp", "Startapp", "EXACT", "startapp token"),
    ("miniapp", "MAX Mini App", "EXACT", "MINI_APP_LOADED"),
    ("identity", "Verified MAX user", "EXACT", "verified initData"),
    ("resolve", "Resolve token", "EXACT", "TOKEN_RESOLVED"),
    ("identity_link", "BotMaxIdentityLink", "EXACT", "BotMaxIdentityLink"),
    ("bot_validation", "Проверка bot_id", "DERIVED", "успешный bot/destination guard"),
    ("bot_open", "Открытие конкретного бота", "ATTEMPT", "BOT_HANDOFF_STARTED; actual open UNKNOWN"),
    ("real_start", "Реальный /start", "EXACT", "REAL_START_RECEIVED"),
    ("revalidation", "Повторная проверка bot_id + BotMaxIdentityLink", "DERIVED", "canonical /start guard"),
    ("restore", "Восстановление BotTrafficVisit / yclid", "EXACT", "paid journey correlation"),
    ("started", "bot_started", "EXACT", "canonical runtime /start"),
    ("metrika", "Metrika", "EXACT", "BotConversion ACCEPTED"),
    ("direct_out", "Direct", "UNKNOWN", "внешняя reporting evidence с задержкой"),
)
JOURNEY_STAGES = {"channel": CHANNEL_STAGES, "bot": BOT_STAGES}


def _journey_period(days: int) -> tuple[datetime, datetime]:
    end = _now()
    return end - timedelta(days=days), end


def _successful_metrika(row: OfflineConversion | BotConversion) -> bool:
    if isinstance(row, BotConversion):
        return row.status == "sent" and row.last_result == "ACCEPTED"
    # Yandex returns UPLOADED before asynchronous matching.  It is not a
    # delivered business conversion yet: the terminal success state is
    # PROCESSED.  Treating UPLOADED as success made the owner dashboard show a
    # false green result while Metrica still displayed "not linked".
    return bool(row.sent_at and row.status == "sent" and str(row.metrica_upload_status or "").upper() in {"PROCESSED", "SUCCESS"})


def _status(*, visits: int, failures: int, warnings: int) -> str:
    if failures:
        return "FAIL"
    if warnings:
        return "WARN"
    return "PASS" if visits else "UNKNOWN"


def _bot_integrity_status(*, visits: int, warnings: int, proven_bypass: bool) -> str:
    """A missing historical telemetry event is not evidence of a /start bypass."""
    return _status(visits=visits, failures=int(proven_bypass), warnings=warnings)


def _stage_payload(definitions: tuple[tuple[str, str, str, str], ...], counts: list[int | None], denominator: int | None) -> list[dict[str, object]]:
    result = []
    previous = None
    for (stage_id, name, evidence_level, evidence), count in zip(definitions, counts):
        ratio = None if count is None or previous in (None, 0) else round(count / previous * 100, 1)
        from_start = None if denominator in (None, 0) or count is None else round(count / denominator * 100, 1)
        result.append({"id": stage_id, "name": name, "count": count, "conversion_from_previous_percent": ratio,
                       "conversion_from_start_percent": from_start, "evidence_level": evidence_level,
                       "evidence": evidence, "status": "UNKNOWN" if count is None else "PASS"})
        if count is not None:
            previous = count
    return result


def _canonical_journeys(visits: list[TrafficVisit] | list[BotTrafficVisit]) -> list[list[TrafficVisit | BotTrafficVisit]]:
    """Group repeated paid landing rows into one journey without merging no-yclid visits."""
    grouped: dict[tuple[str, str], list[TrafficVisit | BotTrafficVisit]] = defaultdict(list)
    for visit in visits:
        grouped[("yclid", visit.yclid) if visit.yclid else ("visit", visit.id)].append(visit)
    return list(grouped.values())


def _main_drop(stages: list[dict[str, object]]) -> dict[str, object] | None:
    measured = [stage for stage in stages if stage["count"] is not None]
    candidates = [(before, after) for before, after in zip(measured, measured[1:]) if int(before["count"]) > 0 and int(after["count"]) < int(before["count"])]
    if not candidates:
        return None
    before, after = max(candidates, key=lambda pair: int(pair[0]["count"]) - int(pair[1]["count"]))
    return {"from": before["name"], "to": after["name"], "from_count": before["count"], "to_count": after["count"],
            "rate": round(int(after["count"]) / int(before["count"]) * 100, 1), "kind": "USER_DROPOFF", "reason": "Пользовательский отвал; техническая причина не доказана"}


def _journey_last_stage(stages: list[dict[str, object]], *, is_bot: bool, real_start: bool, conversion: bool, metrika: bool) -> tuple[str, str]:
    if metrika:
        return "Метрика", "Конверсия передана в Метрику"
    if conversion:
        return ("bot_started", "Конверсия") if is_bot else ("max_subscriber", "Подписка подтверждена")
    if is_bot and real_start:
        return "Реальный /start", "Ожидается конверсия"
    if is_bot and any(stage["id"] == "bot_open" and stage["count"] for stage in stages):
        return "Переход в бот инициирован", "Нет /start"
    return "Нет подтверждённых данных", "В пути"


def canonical_metric_definitions() -> dict[str, dict[str, object]]:
    return {
        "total_clients": {"definition": "count(Admin)", "status": "PROVEN"},
        "paying_clients": {"definition": "distinct Admin with a paid SubscriptionOrder", "status": "PROVEN"},
        "active_subscriptions": {"definition": "Admin.subscription_expires_at > server_now", "status": "PROVEN"},
        "new_clients": {"definition": "Admin.created_at inside requested UTC window", "status": "PROVEN"},
        "new_paying_clients": {"definition": "first paid_at per Admin inside requested UTC window", "status": "PROVEN"},
        "mrr": {"definition": "monthly recurring revenue", "status": "UNKNOWN", "reason": "plans are prepaid and auto-renewal is not implemented"},
        "cancellations": {"definition": "cancelled paid subscriptions", "status": "UNKNOWN", "reason": "no cancellation ledger exists"},
        "conversion": {"definition": "numerator/denominator must be selected from a documented funnel", "status": "UNKNOWN", "reason": "no canonical client conversion funnel exists"},
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _count(db: AsyncSession, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


@router.get("/journey")
async def admin_journey(
    days: int = Query(default=7, ge=1, le=30),
    object_type: str = Query(default="all", pattern="^(all|channel|bot)$"),
    source: str = Query(default="all", pattern="^(all|yandex|organic)$"),
    problem: str = Query(default="all", pattern="^(all|problem|healthy)$"),
    search: str = Query(default="", max_length=120),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    """Bounded read-only journey diagnostics.

    CHANNEL and BOT rows are deliberately loaded and aggregated separately;
    each selected domain retains its own canonical funnel.
    """
    start, end = _journey_period(days)
    needle = search.strip().lower()
    channels = list((await db.scalars(select(Channel).order_by(Channel.title).limit(500))).all())
    profiles = list((await db.scalars(select(BotPromotionProfile).order_by(BotPromotionProfile.bot_key).limit(500))).all())
    connections = list((await db.scalars(select(BotConnection).limit(500))).all())
    connection_by_id = {item.id: item for item in connections}

    channel_visits = [] if object_type == "bot" else list((await db.scalars(
        select(TrafficVisit).where(TrafficVisit.landing_at >= start, TrafficVisit.landing_at <= end)
        .order_by(TrafficVisit.landing_at.desc()).limit(5000)
    )).all())
    channel_visit_ids = [item.id for item in channel_visits]
    channel_events = list((await db.scalars(select(TrafficEvent).where(TrafficEvent.visit_id.in_(channel_visit_ids)))).all()) if channel_visit_ids else []
    channel_links = list((await db.scalars(select(MaxIdentityLink).where(MaxIdentityLink.visitor_id.in_(channel_visit_ids)))).all()) if channel_visit_ids else []
    channel_members = list((await db.scalars(select(TrafficMembership).where(TrafficMembership.attributed_visit_id.in_(channel_visit_ids)))).all()) if channel_visit_ids else []
    channel_joins = list((await db.scalars(select(MaxJoinEvent).where(MaxJoinEvent.channel_id.in_({item.channel_id for item in channel_visits})))).all()) if channel_visits else []
    channel_conversions = list((await db.scalars(select(OfflineConversion).where(OfflineConversion.visit_id.in_(channel_visit_ids)))).all()) if channel_visit_ids else []

    bot_visits = [] if object_type == "channel" else list((await db.scalars(
        select(BotTrafficVisit).where(BotTrafficVisit.created_at >= start, BotTrafficVisit.created_at <= end)
        .order_by(BotTrafficVisit.created_at.desc()).limit(5000)
    )).all())
    bot_visit_ids = [item.id for item in bot_visits]
    bot_events = list((await db.scalars(select(BotTrafficEvent).where(BotTrafficEvent.bot_visit_id.in_(bot_visit_ids)))).all()) if bot_visit_ids else []
    bot_links = list((await db.scalars(select(BotMaxIdentityLink).where(BotMaxIdentityLink.bot_visit_id.in_(bot_visit_ids)))).all()) if bot_visit_ids else []
    bot_conversions = list((await db.scalars(select(BotConversion).where(BotConversion.bot_visit_id.in_(bot_visit_ids)))).all()) if bot_visit_ids else []

    # Paid source is observable through yclid.  Keep the query read-only and do
    # not infer an unobserved advertising source for non-yclid journeys.
    if source != "all":
        channel_visits = [item for item in channel_visits if bool(item.yclid) == (source == "yandex")]
        bot_visits = [item for item in bot_visits if bool(item.yclid) == (source == "yandex")]

    channel_events_by_visit: dict[str, set[str]] = defaultdict(set)
    for event in channel_events:
        channel_events_by_visit[event.visit_id].add(event.event_type)
    bot_events_by_visit: dict[str, set[str]] = defaultdict(set)
    for event in bot_events:
        bot_events_by_visit[event.bot_visit_id].add(event.event_type)
    links_by_visit = {item.visitor_id for item in channel_links}
    bot_links_by_visit = {item.bot_visit_id for item in bot_links}
    channel_members_by_visit = Counter(item.attributed_visit_id for item in channel_members)
    channel_conversions_by_visit = Counter(item.visit_id for item in channel_conversions)
    bot_conversions_by_visit = Counter(item.bot_visit_id for item in bot_conversions)
    joins_by_channel: dict[str, Counter] = defaultdict(Counter)
    for join in channel_joins:
        joins_by_channel[join.channel_id][join.event_type.lower()] += 1

    rows: list[dict[str, object]] = []
    journey_rows: list[dict[str, object]] = []

    channel_by_id = {item.id: item for item in channels}
    channel_visits_by_object: dict[str, list[TrafficVisit]] = defaultdict(list)
    for visit in channel_visits:
        channel_visits_by_object[visit.channel_id].append(visit)
    for channel in channels:
        if object_type == "bot":
            continue
        visits = channel_visits_by_object.get(channel.id, [])
        if needle and needle not in f"{channel.title} {channel.id}".lower():
            continue
        visit_ids = {item.id for item in visits}
        journeys = _canonical_journeys(visits)
        has_event = lambda journey, event: any(event in channel_events_by_visit[item.id] for item in journey)
        has_link = lambda journey: any(item.id in links_by_visit for item in journey)
        has_membership = lambda journey: any(channel_members_by_visit[item.id] for item in journey)
        has_metrika = lambda journey: any(
            _successful_metrika(conversion)
            for visit in journey for conversion in channel_conversions if conversion.visit_id == visit.id
        )
        miniapp = sum(has_event(journey, "MINI_APP_LOADED") for journey in journeys)
        resolved = sum(has_event(journey, "RESOLVE_OK") for journey in journeys)
        handoff = sum(has_event(journey, "MINIAPP_CHANNEL_HANDOFF_STARTED") for journey in journeys)
        identity = sum(has_link(journey) for journey in journeys)
        opened = sum(any(bool(item.to_channel_at) for item in journey) for journey in journeys)
        subscribed = sum(has_membership(journey) for journey in journeys)
        metrica = sum(has_metrika(journey) for journey in journeys)
        # A join request has no visit foreign key.  Preserve the canonical stage
        # but leave it UNKNOWN rather than attributing channel-wide raw events.
        joins = joins_by_channel.get(channel.id, Counter())
        approved = subscribed
        failures = 0
        warnings = int(bool(visits) and (not identity or not opened))
        status = _status(visits=len(visits), failures=failures, warnings=warnings)
        if (problem == "problem" and status not in {"FAIL", "WARN"}) or (problem == "healthy" and status in {"FAIL", "WARN"}):
            continue
        denominator = len(journeys)
        counts = [None, denominator, denominator, sum(any(bool(item.visit_token) for item in journey) for journey in journeys),
                  sum(any(bool(item.startapp_token) for item in journey) for journey in journeys), miniapp, identity, resolved, identity,
                  identity, handoff, None, approved, approved,
                  subscribed, subscribed, subscribed, metrica, None]
        stages = _stage_payload(JOURNEY_STAGES["channel"], counts, denominator)
        rows.append({"id": channel.id, "name": channel.title, "type": "channel", "status": status,
                     "counts": {"landing": len(visits), "identity": identity, "destination": opened, "conversion": subscribed, "metrica": metrica},
                     "journey_denominator": denominator,
                     "conversion_kpi": {"label": "Конверсия в подписчика", "count": subscribed, "rate": None if not denominator else round(subscribed / denominator * 100, 1)},
                     "metrika_rate": None if not denominator else round(metrica / denominator * 100, 1),
                     "stages": stages, "main_drop": _main_drop(stages),
                     "reason": "manual approve pending or incomplete evidence" if status == "WARN" else ("channel stage contradiction" if status == "FAIL" else None)})
        for visit in visits:
            event_types = channel_events_by_visit[visit.id]
            journey_rows.append({"time": visit.landing_at.isoformat(), "object": channel.title, "type": "Канал",
                "source": visit.utm_source or ("Yandex Direct" if visit.yclid else "Неизвестно"), "yclid_present": bool(visit.yclid),
                "identity_resolved": visit.id in links_by_visit, "destination_reached": bool(visit.to_channel_at),
                "authoritative_confirmation": bool(joins_by_channel.get(channel.id, Counter()).get("user_added", 0)),
                "product_conversion": bool(channel_members_by_visit[visit.id]), "metrica": bool(channel_conversions_by_visit[visit.id] and any(_successful_metrika(c) for c in channel_conversions if c.visit_id == visit.id)),
                "direct": "UNKNOWN", "status": "PASS" if channel_members_by_visit[visit.id] else ("WARN" if event_types else "UNKNOWN"),
                "last_stage": ("Метрика", "Конверсия передана в Метрику") if channel_conversions_by_visit[visit.id] else (("max_subscriber", "Подписка подтверждена") if channel_members_by_visit[visit.id] else ("Переход в канал инициирован" if "MINIAPP_CHANNEL_HANDOFF_STARTED" in event_types else "Нет подтверждённых данных", "В пути")),})

    profile_by_bot_id: dict[str, tuple[str, str, str]] = {}
    for profile in profiles:
        connection = connection_by_id.get(profile.bot_connection_id)
        bot_id = connection.max_bot_id if connection else (settings.max_bot_id if profile.bot_type == "SYSTEM" else None)
        if bot_id:
            profile_by_bot_id[bot_id] = (profile.bot_key, "MaxiBot" if profile.bot_type == "SYSTEM" else (connection.display_name if connection else "Бот"), profile.bot_type)
    bot_visits_by_object: dict[str, list[BotTrafficVisit]] = defaultdict(list)
    for visit in bot_visits:
        bot_visits_by_object[visit.bot_id].append(visit)
    for bot_id, visits in bot_visits_by_object.items():
        profile = profile_by_bot_id.get(bot_id, (bot_id, "Бот", "CUSTOMER"))
        name, bot_name, bot_type = profile
        if needle and needle not in f"{bot_name} {name} {bot_id}".lower():
            continue
        visit_ids = {item.id for item in visits}
        journeys = _canonical_journeys(visits)
        has_event = lambda journey, event: any(event in bot_events_by_visit[item.id] for item in journey)
        has_link = lambda journey: any(item.id in bot_links_by_visit for item in journey)
        has_started = lambda journey: any(item.status == "BOT_STARTED" for item in journey)
        has_metrika = lambda journey: any(
            _successful_metrika(conversion)
            for visit in journey for conversion in bot_conversions if conversion.bot_visit_id == visit.id
        )
        handoff_started = sum(has_event(journey, "BOT_HANDOFF_STARTED") for journey in journeys)
        identity = sum(has_link(journey) for journey in journeys)
        real_starts = sum(has_event(journey, "REAL_START_RECEIVED") for journey in journeys)
        started = sum(has_started(journey) for journey in journeys)
        metrica = sum(has_metrika(journey) for journey in journeys)
        # Historical bot_started rows can predate REAL_START_RECEIVED telemetry.
        # Missing telemetry is an observability gap, never proof of a bypass.
        failures = 0
        warnings = int(bool(visits) and (not identity or (identity and not handoff_started) or (handoff_started and not real_starts)))
        status = _bot_integrity_status(visits=len(visits), warnings=warnings, proven_bypass=False)
        if (problem == "problem" and status not in {"FAIL", "WARN"}) or (problem == "healthy" and status in {"FAIL", "WARN"}):
            continue
        miniapp = sum(has_event(journey, "MINI_APP_LOADED") for journey in journeys)
        resolved = sum(has_event(journey, "TOKEN_RESOLVED") for journey in journeys)
        denominator = len(journeys)
        paid_restored = sum(any(item.status == "BOT_STARTED" and bool(item.yclid) for item in journey) for journey in journeys)
        counts = [None, denominator, denominator, denominator, denominator, miniapp, identity, resolved,
                  identity, identity, handoff_started or None, real_starts, real_starts,
                  paid_restored, started, metrica, None]
        stages = _stage_payload(JOURNEY_STAGES["bot"], counts, denominator)
        rows.append({"id": name, "name": bot_name, "type": "bot", "status": status,
                     "counts": {"landing": len(visits), "identity": identity, "destination": None, "conversion": started, "metrica": metrica},
                     "journey_denominator": denominator,
                     "conversion_kpi": {"label": "Конверсия в bot_started", "count": started, "rate": None if not denominator else round(started / denominator * 100, 1)},
                     "metrika_rate": None if not denominator else round(metrica / denominator * 100, 1),
                     "stages": stages, "main_drop": _main_drop(stages),
                     "reason": "Переход в бот инициирован, /start не получен" if status == "WARN" and identity and handoff_started and not real_starts else ("Историческая observability gap: нет отдельного REAL_START_RECEIVED" if started and not real_starts else None)})
        for visit in visits:
            event_types = bot_events_by_visit[visit.id]
            journey_rows.append({"time": visit.created_at.isoformat(), "object": bot_name, "type": "Бот",
                "source": visit.utm_source or ("Yandex Direct" if visit.yclid else "Неизвестно"), "yclid_present": bool(visit.yclid),
                "identity_resolved": visit.id in bot_links_by_visit, "handoff_started": visit.id in bot_events_by_visit and "BOT_HANDOFF_STARTED" in bot_events_by_visit[visit.id], "destination_reached": "UNKNOWN",
                "real_start": visit.id in bot_events_by_visit and "REAL_START_RECEIVED" in bot_events_by_visit[visit.id],
                "product_conversion": visit.status == "BOT_STARTED", "metrica": bool(bot_conversions_by_visit[visit.id] and any(_successful_metrika(c) for c in bot_conversions if c.bot_visit_id == visit.id)),
                "direct": "UNKNOWN", "status": "PASS" if visit.status == "BOT_STARTED" else ("WARN" if event_types else "UNKNOWN"),
                "last_stage": _journey_last_stage(stages, is_bot=True, real_start="REAL_START_RECEIVED" in event_types, conversion=visit.status == "BOT_STARTED", metrika=bool(bot_conversions_by_visit[visit.id])),})

    journey_rows.sort(key=lambda item: item["time"], reverse=True)
    selected_rows = [item for item in journey_rows if object_type == "all" or (object_type == "channel" and item["type"] == "Канал") or (object_type == "bot" and item["type"] == "Бот")][:100]
    problem_objects = [item for item in rows if item["status"] in {"FAIL", "WARN"}]
    all_journeys = sum(int(item["journey_denominator"]) for item in rows)

    def aggregate_domain(kind: str) -> dict[str, object] | None:
        members = [item for item in rows if item["type"] == kind]
        if not members:
            return None
        definitions = JOURNEY_STAGES[kind]
        counts: list[int | None] = []
        for index in range(len(definitions)):
            observed = [item["stages"][index]["count"] for item in members if item["stages"][index]["count"] is not None]
            counts.append(sum(int(value) for value in observed) if observed else None)
        denominator = sum(int(item["journey_denominator"]) for item in members)
        final_key = "conversion"
        final_count = sum(int(item["counts"][final_key]) for item in members)
        metrika_count = sum(int(item["counts"]["metrica"]) for item in members)
        return {
            "id": f"all-{kind}",
            "name": "Все каналы" if kind == "channel" else "Все боты",
            "type": kind,
            "status": "FAIL" if any(item["status"] == "FAIL" for item in members) else ("WARN" if any(item["status"] == "WARN" for item in members) else "PASS"),
            "journey_denominator": denominator,
            "conversion_kpi": {
                "label": "Конверсия в подписчика" if kind == "channel" else "Конверсия в bot_started",
                "count": final_count,
                "rate": None if not denominator else round(final_count / denominator * 100, 1),
            },
            "metrika_rate": None if not denominator else round(metrika_count / denominator * 100, 1),
            "stages": _stage_payload(definitions, counts, denominator),
        }

    domain_funnels = [item for item in (aggregate_domain("channel"), aggregate_domain("bot")) if item is not None]
    for item in domain_funnels:
        item["main_drop"] = _main_drop(item["stages"])
    return {"read_only": True, "scope": {"from": start.isoformat(), "to": end.isoformat(), "days": days, "timezone": "UTC"},
            "filters": {"type": object_type, "source": source, "problem": problem, "search": search},
            "summary": {"channels": sum(item["type"] == "channel" for item in rows), "bots": sum(item["type"] == "bot" for item in rows),
                        "active_journeys": all_journeys, "problem_objects": len(problem_objects),
                        "overall_status": "FAIL" if any(item["status"] == "FAIL" for item in rows) else ("WARN" if problem_objects else ("PASS" if rows else "UNKNOWN"))},
            "domain_funnels": domain_funnels,
            "normalized_funnel": [],
            "objects": rows, "problem_objects": problem_objects, "recent_journeys": selected_rows,
            "semantics": {"channel_manual_approve": True, "bot_real_start_distinct_from_bot_started": True, "direct": "UNKNOWN when no confirmation source exists"}}


@router.get("/overview")
async def admin_overview(
    window_days: int = Query(default=30, ge=1, le=365),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    now = _now()
    start = now - timedelta(days=window_days)
    total_clients = await _count(db, Admin)
    paying_clients = int(await db.scalar(select(func.count(distinct(SubscriptionOrder.admin_id))).where(SubscriptionOrder.status == "paid")) or 0)
    active_subscriptions = int(await db.scalar(select(func.count()).select_from(Admin).where(Admin.subscription_expires_at > now)) or 0)
    new_clients = int(await db.scalar(select(func.count()).select_from(Admin).where(Admin.created_at >= start)) or 0)
    first_paid = select(SubscriptionOrder.admin_id, func.min(SubscriptionOrder.paid_at).label("first_paid_at")).where(SubscriptionOrder.status == "paid").group_by(SubscriptionOrder.admin_id).subquery()
    new_paying = int(await db.scalar(select(func.count()).select_from(first_paid).where(first_paid.c.first_paid_at >= start)) or 0)
    windows = {}
    for days in (1, 7, 30):
        window_start = now - timedelta(days=days)
        windows[str(days)] = {
            "from": window_start.isoformat(),
            "to": now.isoformat(),
            "new_clients": int(await db.scalar(select(func.count()).select_from(Admin).where(Admin.created_at >= window_start)) or 0),
            "new_paying_clients": int(await db.scalar(select(func.count()).select_from(first_paid).where(first_paid.c.first_paid_at >= window_start)) or 0),
        }
    return {
        "scope": {"window_days": window_days, "from": start.isoformat(), "to": now.isoformat(), "timezone": "UTC"},
        "windows": windows,
        "metrics": {
            "total_clients": total_clients,
            "paying_clients": paying_clients,
            "active_subscriptions": active_subscriptions,
            "new_clients": new_clients,
            "new_paying_clients": new_paying,
            "mrr": None,
            "cancellations": None,
            "conversion": {"numerator": None, "denominator": None, "status": "UNKNOWN"},
        },
        "definitions": canonical_metric_definitions(),
    }


@router.get("/telegram-ads")
async def admin_telegram_ads(
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    """Read-only Telegram Ads connection/inventory health; secrets excluded."""
    accounts = list((await db.scalars(select(AdsAccount).where(AdsAccount.provider == "TELEGRAM_ADS").order_by(AdsAccount.updated_at.desc()))).all())
    items = []
    for account in accounts:
        identity = await db.get(TelegramAdsServiceIdentity, account.telegram_service_identity_id) if account.telegram_service_identity_id else None
        ad_count = await db.scalar(select(func.count()).select_from(AdsCampaign).where(AdsCampaign.account_id == account.id))
        latest_run = await db.scalar(select(AdsCollectionRun).where(AdsCollectionRun.account_id == account.id).order_by(AdsCollectionRun.completed_at.desc()).limit(1))
        latest_health = await db.scalar(select(AdsDataHealth).where(AdsDataHealth.promotion_object_id == account.id).order_by(AdsDataHealth.checked_at.desc()).limit(1))
        items.append({
            "provider": "Telegram Ads",
            "organization": account.display_name,
            "external_account_id": account.external_account_id,
            "service_identity_id": identity.id if identity else None,
            "connection_status": account.status,
            "storage_region": account.storage_region or "RU",
            "execution_region": account.execution_region or "RU",
            "session_status": identity.status if identity else "UNKNOWN",
            "last_owner_verification": identity.last_verified_at.isoformat() if identity and identity.last_verified_at else None,
            "last_collection": latest_run.completed_at.isoformat() if latest_run and latest_run.completed_at else None,
            "ads_count": int(ad_count or 0),
            "data_health": latest_health.status if latest_health else "UNKNOWN",
            "writes": "DISABLED",
            "autopilot": "DISABLED",
        })
    return {"read_only": True, "items": items}


@router.get("/clients")
async def admin_clients(
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    admins = list((await db.scalars(select(Admin).order_by(Admin.created_at.desc()).limit(limit))).all())
    rows = []
    for admin in admins:
        channel_count = await db.scalar(select(func.count()).select_from(Channel).where(Channel.admin_id == admin.id))
        paid_orders = await db.scalar(select(func.count()).select_from(SubscriptionOrder).where(SubscriptionOrder.admin_id == admin.id, SubscriptionOrder.status == "paid"))
        rows.append({
            "client_id": admin.id,
            "created_at": admin.created_at.isoformat() if admin.created_at else None,
            "channel_count": int(channel_count or 0),
            "paid_order_count": int(paid_orders or 0),
            "subscription_expires_at": admin.subscription_expires_at.isoformat() if admin.subscription_expires_at else None,
            "active_subscription": bool(admin.subscription_expires_at and admin.subscription_expires_at > _now()),
        })
    return {"items": rows, "read_only": True}


@router.get("/subscriptions")
async def admin_subscriptions(
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    orders = list((await db.scalars(select(SubscriptionOrder).order_by(SubscriptionOrder.created_at.desc()).limit(limit))).all())
    return {
        "items": [
            {
                "order_id": order.id,
                "client_id": order.admin_id,
                "channel_id": order.channel_id,
                "months": order.months,
                "amount": float(order.amount),
                "status": order.status,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
            }
            for order in orders
        ],
        "read_only": True,
    }


@router.get("/product-usage")
async def admin_product_usage(
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    now = _now()
    today = now - timedelta(days=1)
    week = now - timedelta(days=7)
    return {
        "counts": {
            "channels": await _count(db, Channel),
            "bots": await _count(db, BotConnection),
            "conversations": await _count(db, Conversation),
            "connected_channels": int(await db.scalar(select(func.count()).select_from(Channel).where(Channel.external_id.is_not(None))) or 0),
            "yandex_connected_channels": int(await db.scalar(select(func.count()).select_from(TrafficProfile).where(TrafficProfile.yandex_enabled.is_(True), TrafficProfile.yandex_oauth_token_encrypted.is_not(None))) or 0),
            "metrica_connected_channels": int(await db.scalar(select(func.count()).select_from(TrafficProfile).where(TrafficProfile.yandex_counter_id.is_not(None))) or 0),
            "daily_reports_enabled_channels": int(await db.scalar(select(func.count()).select_from(Channel).where(Channel.daily_reports_enabled.is_(True))) or 0),
            "ai_runs": await _count(db, AdsAiRun),
            "ai_runs_today": int(await db.scalar(select(func.count()).select_from(AdsAiRun).where(AdsAiRun.created_at >= today)) or 0),
            "ai_runs_7d": int(await db.scalar(select(func.count()).select_from(AdsAiRun).where(AdsAiRun.created_at >= week)) or 0),
            "active_clients": int(await db.scalar(select(func.count()).select_from(Admin).where(Admin.subscription_expires_at > now)) or 0),
        },
        "read_only": True,
    }


@router.get("/system-health")
async def admin_system_health(
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    await db.scalar(select(func.count()).select_from(Admin))
    return {
        "service": "maxibot",
        "api": {"status": "ready", "release_id": settings.maxibot_release_id},
        "max": {"mode": settings.max_client_mode},
        "database": {"status": "ready"},
        "read_only": True,
    }


def _qa_parity(run: QaRun) -> dict[str, str]:
    configured_sha = settings.maxibot_release_id if len(settings.maxibot_release_id or "") == 40 else UNKNOWN
    current_main_sha = run.current_main_sha or configured_sha
    main = parity_status(run.tested_sha, current_main_sha)
    production = parity_status(run.tested_sha, run.production_sha)
    overall = "MISMATCH" if "MISMATCH" in {main, production} else ("MATCH" if main == "MATCH" else "UNKNOWN")
    return {"overall": overall, "tested_vs_current_main": main, "tested_vs_production": production}


def _qa_freshness(run: QaRun) -> dict[str, object]:
    if not run.finished_at:
        return {"status": "UNKNOWN", "finished_at": None, "age_seconds": None}
    age = max(int((_now() - run.finished_at).total_seconds()), 0)
    return {"status": "FRESH" if age <= 86400 else "STALE", "finished_at": run.finished_at.isoformat(), "age_seconds": age}


def _serialize_qa_run(run: QaRun, *, detail: bool = False) -> dict[str, object]:
    parity = _qa_parity(run)
    current_main_sha = run.current_main_sha or (settings.maxibot_release_id if len(settings.maxibot_release_id or "") == 40 else UNKNOWN)
    payload: dict[str, object] = {
        "run_id": run.id,
        "release_id": run.release_id,
        "TESTED_SHA": run.tested_sha,
        "CURRENT_MAIN_SHA": current_main_sha,
        "PRODUCTION_SHA": run.production_sha or "UNKNOWN",
        "parity": parity,
        "freshness": _qa_freshness(run),
        "verdict": run.verdict,
        "counts": {"total_tests": run.total_tests, "passed": run.passed, "failed": run.failed, "skipped": run.skipped},
        "production": {"status": run.production_status, "smoke": run.production_smoke},
        "production_smoke": run.production_smoke,
        "artifact_sha256": run.artifact_sha256,
        "read_only": True,
    }
    if detail:
        payload["timing"] = {"started_at": run.started_at.isoformat() if run.started_at else None, "finished_at": run.finished_at.isoformat() if run.finished_at else None, "duration_ms": run.duration_ms}
        payload["suite_matrix"] = [
            {
                "name": suite.suite_name,
                "status": suite.status,
                "total_tests": suite.total_tests,
                "passed": suite.passed,
                "failed": suite.failed,
                "skipped": suite.skipped,
                "duration_ms": suite.duration_ms,
                "failures": decode_json(suite.failures_json, []),
                "unverified": decode_json(suite.unverified_json, []),
            }
            for suite in sorted(run.suites, key=lambda item: item.suite_name)
        ]
        payload["failures_sanitized"] = decode_json(run.failures_json, [])
        payload["unverified"] = decode_json(run.unverified_json, {"total": 0, "critical": 0, "statuses": ["WORKING_UNTESTED", "UNKNOWN"]})
        payload["source_path"] = run.source_path
    return payload


@router.post("/qa-runs/ingest", dependencies=[Depends(require_qa_ingest_token)])
async def admin_qa_ingest(payload: dict = Body(...), db: AsyncSession = Depends(get_session)):
    try:
        run = await persist_qa_result(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"run_id": run.id, "release_id": run.release_id, "stored": True, "read_only": False}


@router.get("/qa/current")
async def admin_qa_current(_: str = Depends(require_web_admin), db: AsyncSession = Depends(get_session)):
    run = await db.scalar(select(QaRun).options(selectinload(QaRun.suites)).order_by(QaRun.created_at.desc()).limit(1))
    if not run:
        return {"status": "UNKNOWN", "reason": "no persisted QA run artifact", "read_only": True}
    return _serialize_qa_run(run, detail=True)


@router.get("/qa/runs")
async def admin_qa_runs(
    limit: int = Query(default=10, ge=1, le=10),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    runs = list((await db.scalars(select(QaRun).order_by(QaRun.created_at.desc()).limit(limit))).all())
    return {"items": [_serialize_qa_run(run) for run in runs], "read_only": True}


@router.get("/qa/runs/{run_id}")
async def admin_qa_run(run_id: str, _: str = Depends(require_web_admin), db: AsyncSession = Depends(get_session)):
    run = await db.scalar(select(QaRun).options(selectinload(QaRun.suites)).where(QaRun.id == run_id))
    if not run:
        raise HTTPException(status_code=404, detail="QA run not found")
    return _serialize_qa_run(run, detail=True)


@router.get("/qa-release")
async def admin_qa_release(_: str = Depends(require_web_admin), db: AsyncSession = Depends(get_session)):
    current = await admin_qa_current(_, db)
    if current.get("status") == "UNKNOWN":
        return current
    runs = list((await db.scalars(select(QaRun).order_by(QaRun.created_at.desc()).limit(10))).all())
    verdict = "PASS" if current["verdict"] == "GO" else current["verdict"]
    unverified = current.get("unverified", {})
    if isinstance(unverified, dict):
        unverified = [
            {"name": "UNKNOWN cases", "status": str(unverified.get("total", 0))},
            {"name": "UNKNOWN critical cases", "status": str(unverified.get("critical", 0))},
        ]
    return {
        "release_id": current["release_id"],
        "qa_status": verdict,
        "production_deploy": current["production"]["status"],
        "current_summary": {
            "verdict": verdict,
            "title": "Проверка пройдена" if verdict == "PASS" else "Проверка не пройдена",
            "reason": "Структурированный QA-артефакт сохранён" if verdict == "PASS" else "QA verdict не подтверждён",
            "tested_sha": current["TESTED_SHA"],
            "current_main_sha": current["CURRENT_MAIN_SHA"],
            "production_sha": current["PRODUCTION_SHA"],
            "checked_at": current["freshness"]["finished_at"],
            "parity": current["parity"]["overall"],
        },
        "headline_counts": current["counts"],
        "suite_matrix": current.get("suite_matrix", []),
        "failures": current.get("failures_sanitized", []),
        "unverified": unverified,
        "recent_runs": [_serialize_qa_run(run) for run in runs],
        "read_only": True,
    }


@router.get("/ads-storage")
async def admin_ads_storage(
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    storage = await ads_storage_diagnostics(db)
    return {
        "counts": {
            "ai_runs": await _count(db, AdsAiRun),
            "snapshots": await _count(db, AdsAnalysisSnapshot),
            "memories": await _count(db, AdsMemory),
        },
        "secrets_exposed": False,
        "storage": storage,
        "read_only": True,
    }
