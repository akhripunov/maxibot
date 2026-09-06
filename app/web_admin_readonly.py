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
from app.db.models import (
    Admin,
    AdsAiRun,
    AdsMemory,
    AdsSnapshot,
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


JOURNEY_STAGES = {
    "channel": ("Источник", "Landing", "Token / MAX", "MAX identity", "Канал открыт", "Join request", "user_added", "Подписка", "Metrika", "Direct"),
    "bot": ("Источник", "Landing", "Token / MAX", "MAX identity", "Handoff подтверждён", "Destination reached", "Real /start", "bot_started", "Metrika", "Direct"),
}


def _journey_period(days: int) -> tuple[datetime, datetime]:
    end = _now()
    return end - timedelta(days=days), end


def _successful_metrika(row: OfflineConversion | BotConversion) -> bool:
    if isinstance(row, BotConversion):
        return row.status == "sent" and row.last_result == "ACCEPTED"
    return bool(row.sent_at or row.status in {"sent", "uploaded"} or row.metrica_upload_status in {"processed", "success"})


def _status(*, visits: int, failures: int, warnings: int) -> str:
    if failures:
        return "FAIL"
    if warnings:
        return "WARN"
    return "PASS" if visits else "UNKNOWN"


def _stage_payload(names: tuple[str, ...], counts: list[int]) -> list[dict[str, object]]:
    result = []
    previous = None
    for name, count in zip(names, counts):
        ratio = None if previous in (None, 0) else round(count / previous * 100, 1)
        result.append({"name": name, "count": count, "conversion_from_previous_percent": ratio,
                       "status": "UNKNOWN" if count is None else "PASS"})
        previous = count
    return result


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
    problem: str = Query(default="all", pattern="^(all|problem|healthy)$"),
    search: str = Query(default="", max_length=120),
    _: str = Depends(require_web_admin),
    db: AsyncSession = Depends(get_session),
):
    """Bounded read-only cross-domain journey diagnostics.

    CHANNEL and BOT rows are deliberately loaded and aggregated separately;
    the normalized view below contains only comparable milestones.
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
        handoff = sum(bool(channel_events_by_visit[item.id] & {"MAX_REDIRECT_SENT", "HANDOFF_CONFIRMED", "MINI_APP_LOADED", "RESOLVE_OK"}) for item in visits)
        identity = sum(item.visitor_id in visit_ids for item in channel_links)
        opened = sum(bool(item.to_channel_at) for item in visits)
        joins = joins_by_channel.get(channel.id, Counter())
        approved = joins.get("user_added", 0)
        subscribed = sum(channel_members_by_visit[item.id] for item in visits)
        metrica = sum(_successful_metrika(item) for item in channel_conversions if item.visit_id in visit_ids)
        failures = int(approved > subscribed or (subscribed and metrica == 0 and any(item.yclid for item in visits)))
        warnings = int(bool(visits) and (not identity or not opened))
        status = _status(visits=len(visits), failures=failures, warnings=warnings)
        if (problem == "problem" and status not in {"FAIL", "WARN"}) or (problem == "healthy" and status in {"FAIL", "WARN"}):
            continue
        counts = [len(visits), sum(bool(item.startapp_token) for item in visits), handoff, identity, opened, joins.get("join_request", 0), approved, subscribed, metrica, None]
        rows.append({"id": channel.id, "name": channel.title, "type": "channel", "status": status,
                     "counts": {"landing": len(visits), "identity": identity, "destination": opened, "conversion": subscribed, "metrica": metrica},
                     "stages": _stage_payload(JOURNEY_STAGES["channel"], counts),
                     "reason": "manual approve pending or incomplete evidence" if status == "WARN" else ("channel stage contradiction" if status == "FAIL" else None)})
        for visit in visits:
            event_types = channel_events_by_visit[visit.id]
            journey_rows.append({"time": visit.landing_at.isoformat(), "object": channel.title, "type": "Канал",
                "source": visit.utm_source or ("Yandex Direct" if visit.yclid else "Неизвестно"), "yclid_present": bool(visit.yclid),
                "identity_resolved": visit.id in links_by_visit, "destination_reached": bool(visit.to_channel_at),
                "product_conversion": bool(channel_members_by_visit[visit.id]), "metrica": bool(channel_conversions_by_visit[visit.id] and any(_successful_metrika(c) for c in channel_conversions if c.visit_id == visit.id)),
                "direct": "UNKNOWN", "status": "PASS" if channel_members_by_visit[visit.id] else ("WARN" if event_types else "UNKNOWN")})

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
        handoff = sum(bool(bot_events_by_visit[item.id] & {"BOT_HANDOFF_CONFIRMED", "HANDOFF_CONFIRMED", "MINI_APP_LOADED"}) for item in visits)
        identity = sum(item.bot_visit_id in visit_ids for item in bot_links)
        real_starts = sum("REAL_START_RECEIVED" in bot_events_by_visit[item.id] for item in visits)
        started = sum(item.status == "BOT_STARTED" for item in visits)
        metrica = sum(_successful_metrika(item) for item in bot_conversions if item.bot_visit_id in visit_ids)
        failures = int(started > real_starts or any(item.bot_visit_id in visit_ids and item.bot_visit_id not in bot_links_by_visit for item in bot_conversions))
        warnings = int(bool(visits) and (not identity or (identity and not real_starts)))
        status = _status(visits=len(visits), failures=failures, warnings=warnings)
        if (problem == "problem" and status not in {"FAIL", "WARN"}) or (problem == "healthy" and status in {"FAIL", "WARN"}):
            continue
        counts = [len(visits), len(visits), handoff, identity, handoff, None, real_starts, started, metrica, None]
        rows.append({"id": name, "name": bot_name, "type": "bot", "status": status,
                     "counts": {"landing": len(visits), "identity": identity, "destination": None, "conversion": started, "metrica": metrica},
                     "stages": _stage_payload(JOURNEY_STAGES["bot"], counts),
                     "reason": "bot_started without real /start" if status == "FAIL" else ("partial handoff evidence" if status == "WARN" else None)})
        for visit in visits:
            event_types = bot_events_by_visit[visit.id]
            journey_rows.append({"time": visit.created_at.isoformat(), "object": bot_name, "type": "Бот",
                "source": visit.utm_source or ("Yandex Direct" if visit.yclid else "Неизвестно"), "yclid_present": bool(visit.yclid),
                "identity_resolved": visit.id in bot_links_by_visit, "destination_reached": "UNKNOWN",
                "product_conversion": visit.status == "BOT_STARTED", "metrica": bool(bot_conversions_by_visit[visit.id] and any(_successful_metrika(c) for c in bot_conversions if c.bot_visit_id == visit.id)),
                "direct": "UNKNOWN", "status": "PASS" if visit.status == "BOT_STARTED" else ("WARN" if event_types else "UNKNOWN")})

    journey_rows.sort(key=lambda item: item["time"], reverse=True)
    selected_rows = [item for item in journey_rows if object_type == "all" or (object_type == "channel" and item["type"] == "Канал") or (object_type == "bot" and item["type"] == "Бот")][:100]
    problem_objects = [item for item in rows if item["status"] in {"FAIL", "WARN"}]
    all_visits = sum(int(item["counts"]["landing"]) for item in rows)
    destination_counts = [item["counts"]["destination"] for item in rows if item["counts"]["destination"] is not None]
    normalized = [all_visits, all_visits, sum(int(stage["count"]) for item in rows for stage in item["stages"] if stage["name"] == "MAX identity"),
                  sum(destination_counts) if destination_counts else None, sum(item["counts"]["conversion"] for item in rows),
                  sum(item["counts"]["metrica"] for item in rows), None]
    return {"read_only": True, "scope": {"from": start.isoformat(), "to": end.isoformat(), "days": days, "timezone": "UTC"},
            "filters": {"type": object_type, "problem": problem, "search": search},
            "summary": {"channels": sum(item["type"] == "channel" for item in rows), "bots": sum(item["type"] == "bot" for item in rows),
                        "active_journeys": all_visits, "problem_objects": len(problem_objects),
                        "overall_status": "FAIL" if any(item["status"] == "FAIL" for item in rows) else ("WARN" if problem_objects else ("PASS" if rows else "UNKNOWN"))},
            "normalized_funnel": _stage_payload(("Источник", "Landing", "MAX identity", "Destination reached", "Product conversion", "Metrika", "Direct"), normalized),
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
    return {
        "counts": {
            "ai_runs": await _count(db, AdsAiRun),
            "snapshots": await _count(db, AdsSnapshot),
            "memories": await _count(db, AdsMemory),
        },
        "secrets_exposed": False,
        "read_only": True,
    }
