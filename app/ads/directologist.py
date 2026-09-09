"""Deterministic, read-only decision-engine primitives for AI Promotion.

This module is intentionally independent from the LLM.  It produces bounded
metrics, comparison windows, attribution-quality labels, and conservative
decision states.  Provider conversions and confirmed MaxiBot outcomes are
kept as separate populations.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    AdsCampaign, AdsDailyAggregate, AdsPromotionCampaignLink, BotConversion,
    BotPromotionProfile,
    BotTrafficVisit, TrafficMembership, TrafficVisit,
)

MOSCOW = __import__("zoneinfo").ZoneInfo("Europe/Moscow")
WINDOW_DAYS = 7


def comparison_windows(*, today: date, campaign_start: date | None = None) -> dict[str, Any]:
    """Return two comparable full-day windows; never includes the current day."""
    current_end = today - timedelta(days=1)
    current_start = current_end - timedelta(days=WINDOW_DAYS - 1)
    baseline_end = current_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=WINDOW_DAYS - 1)
    if campaign_start and campaign_start > current_start:
        status = "INSUFFICIENT_DATA"
    else:
        status = "READY"
    return {
        "current": {"start": current_start.isoformat(), "end": current_end.isoformat(), "days": WINDOW_DAYS},
        "baseline": {"start": baseline_start.isoformat(), "end": baseline_end.isoformat(), "days": WINDOW_DAYS},
        "current_day_excluded": today.isoformat(),
        "partial_day_guard": "PASS",
        "status": status,
    }


def _metric(values: dict[str, float], name: str) -> float | None:
    impressions = values.get("impressions", 0.0)
    clicks = values.get("clicks", 0.0)
    cost = values.get("cost", 0.0)
    conversions = values.get("conversions", 0.0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "cost": cost,
        "conversions": conversions,
        "ctr": clicks / impressions if impressions else None,
        "cpc": cost / clicks if clicks else None,
        "provider_cvr": conversions / clicks if clicks else None,
        "provider_cpa": cost / conversions if conversions else None,
    }[name]


def _period_metrics(rows: Iterable[Any], start: date, end: date) -> dict[str, Any]:
    values = defaultdict(float)
    dates: set[date] = set()
    for row in rows:
        aggregate_date = getattr(row, "aggregate_date", None)
        if getattr(row, "dimension_type", None) != "CAMPAIGN" or not aggregate_date or not start <= aggregate_date <= end:
            continue
        dates.add(aggregate_date)
        for name in ("impressions", "clicks", "cost", "conversions"):
            value = getattr(row, name, None)
            if value is not None:
                values[name] += float(value)
    result = {name: _metric(values, name) for name in ("impressions", "clicks", "cost", "conversions", "ctr", "cpc", "provider_cvr", "provider_cpa")}
    result["days_present"] = len(dates)
    result["expected_days"] = (end - start).days + 1
    result["complete"] = len(dates) == result["expected_days"]
    return result


def _delta(current: float | None, baseline: float | None) -> dict[str, float | None]:
    return {
        "absolute": current - baseline if current is not None and baseline is not None else None,
        "relative": (current - baseline) / baseline if current is not None and baseline not in (None, 0) else None,
    }


def comparative_metrics(rows: Iterable[Any], windows: dict[str, Any]) -> dict[str, Any]:
    current = _period_metrics(rows, date.fromisoformat(windows["current"]["start"]), date.fromisoformat(windows["current"]["end"]))
    baseline = _period_metrics(rows, date.fromisoformat(windows["baseline"]["start"]), date.fromisoformat(windows["baseline"]["end"]))
    return {
        "current": current,
        "baseline": baseline,
        "delta": {name: _delta(current.get(name), baseline.get(name)) for name in ("impressions", "clicks", "cost", "conversions", "ctr", "cpc", "provider_cvr", "provider_cpa")},
        "status": "READY" if current["complete"] and baseline["complete"] else "INSUFFICIENT_DATA",
        "source": "ads_daily_aggregates campaign dimension only",
    }


def classify_trend(*, current: float | None, baseline: float | None, metric: str, clicks: float | None, confirmed_outcomes: int | None) -> str:
    if current is None or baseline is None or clicks is None or clicks < 10:
        return "INSUFFICIENT_DATA"
    if metric in {"confirmed_cpa", "provider_cpa", "cpc"} and (confirmed_outcomes is not None and metric == "confirmed_cpa" and confirmed_outcomes < 3):
        return "INSUFFICIENT_DATA"
    if baseline == 0:
        return "INSUFFICIENT_DATA"
    relative = (current - baseline) / baseline
    if abs(relative) <= 0.05:
        return "STABLE"
    improving = relative < 0 if metric in {"confirmed_cpa", "provider_cpa", "cpc"} else relative > 0
    return "IMPROVING" if improving else "DEGRADING"


def attribution_quality(*, has_yclid: bool, campaign_match: bool | None, deterministic_link: bool) -> str:
    if deterministic_link and campaign_match:
        return "MATCHED"
    if campaign_match is False:
        return "UNRESOLVED"
    if deterministic_link and has_yclid:
        return "OBSERVED"
    if not has_yclid:
        return "UNATTRIBUTED"
    return "UNRESOLVED"


def _campaign_match_json(value: str | None, external_id: str) -> bool | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    text = json.dumps(data, ensure_ascii=False)
    return external_id in text


async def confirmed_outcome_summary(
    db: AsyncSession,
    *,
    campaign: AdsCampaign,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    """Read canonical CHANNEL/BOT outcomes without treating Metrika as truth."""
    link = await db.scalar(select(AdsPromotionCampaignLink).where(
        AdsPromotionCampaignLink.campaign_id == campaign.id,
        AdsPromotionCampaignLink.status == "ACTIVE",
    ).limit(1))
    object_type = link.object_type if link else "UNKNOWN"
    rows: list[dict[str, Any]] = []
    if object_type == "BOT":
        profile = await db.get(BotPromotionProfile, link.bot_promotion_profile_id) if link and link.bot_promotion_profile_id else None
        conversions = list((await db.scalars(select(BotConversion).join(BotTrafficVisit, BotTrafficVisit.id == BotConversion.bot_visit_id).where(
            BotConversion.internal_event == "bot_started",
            BotTrafficVisit.bot_id == (profile.bot_key if profile else "__missing_bot__"),
            BotConversion.created_at >= window_start,
            BotConversion.created_at < window_end,
        ))).all())
        for conversion in conversions:
            visit = await db.get(BotTrafficVisit, conversion.bot_visit_id)
            yclid = visit.yclid if visit else None
            campaign_match = bool(visit and visit.utm_campaign == campaign.external_id)
            rows.append({"id": conversion.id, "type": "BOT", "has_yclid": bool(yclid), "quality": attribution_quality(has_yclid=bool(yclid), campaign_match=campaign_match, deterministic_link=True), "source": "BotConversion.bot_started", "dedupe_key": conversion.paid_journey_key or conversion.bot_visit_id})
    elif object_type == "CHANNEL":
        membership_query = select(TrafficMembership).join(
            TrafficVisit, TrafficVisit.id == TrafficMembership.attributed_visit_id
        ).where(
            TrafficMembership.joined_at >= window_start,
            TrafficMembership.joined_at < window_end,
            TrafficMembership.attribution_status.in_(("EXACT", "HIGH")),
        )
        if link and link.channel_id:
            membership_query = membership_query.where(TrafficMembership.channel_id == link.channel_id)
        memberships = list((await db.scalars(membership_query)).all())
        for membership in memberships:
            visit = await db.get(TrafficVisit, membership.attributed_visit_id) if membership.attributed_visit_id else None
            yclid = visit.yclid if visit else None
            campaign_match = _campaign_match_json(visit.campaign_metadata_json if visit else None, campaign.external_id)
            rows.append({"id": membership.id, "type": "CHANNEL", "has_yclid": bool(yclid), "quality": attribution_quality(has_yclid=bool(yclid), campaign_match=campaign_match, deterministic_link=True), "source": "TrafficMembership", "dedupe_key": f"{membership.channel_id}:{membership.max_user_id}"})
    deduped = {row["dedupe_key"]: row for row in rows}
    attributed = [row for row in deduped.values() if row["quality"] in {"OBSERVED", "MATCHED"}]
    return {
        "object_type": object_type,
        "confirmed_total": len(deduped),
        "attributed_outcomes": len(attributed),
        "unattributed_outcomes": sum(row["quality"] == "UNATTRIBUTED" for row in deduped.values()),
        "unresolved_count": sum(row["quality"] == "UNRESOLVED" for row in deduped.values()),
        "attribution_coverage": len(attributed) / len(deduped) if deduped else None,
        "quality_counts": {quality: sum(row["quality"] == quality for row in deduped.values()) for quality in ("OBSERVED", "MATCHED", "MODELED", "UNRESOLVED", "UNATTRIBUTED")},
        "rows": list(deduped.values()),
        "source_of_truth": "TrafficMembership/max_subscriber or BotConversion/bot_started; Metrika is separate",
    }


def confidence(*, clicks: float | None, confirmed_outcomes: int | None, freshness_hours: float | None, attribution_coverage: float | None, root_cause_proven: bool, windows_ready: bool) -> dict[str, Any]:
    reasons: list[str] = []
    if not windows_ready:
        reasons.append("сопоставимые полные окна недоступны")
    if clicks is None or clicks < 10:
        reasons.append("малый объём кликов")
    if confirmed_outcomes is None or confirmed_outcomes < 3:
        reasons.append("мало подтверждённых бизнес-результатов")
    if freshness_hours is None or freshness_hours > 48:
        reasons.append("свежесть данных недостаточна")
    if attribution_coverage is None or attribution_coverage < 0.8:
        reasons.append("покрытие атрибуции недостаточно")
    if not root_cause_proven:
        reasons.append("root cause не доказан")
    if reasons:
        level = "INSUFFICIENT" if len(reasons) >= 3 or not windows_ready else "LOW"
    elif clicks >= 100 and confirmed_outcomes >= 10 and attribution_coverage >= 0.9 and (freshness_hours or 0) <= 24:
        level = "HIGH"
    else:
        level = "MEDIUM"
    return {"level": level, "reasons": reasons}


def root_cause_status(*, contributors: list[dict[str, Any]], sufficient: bool) -> dict[str, Any]:
    if not sufficient:
        return {"status": "ROOT_CAUSE_NOT_PROVEN", "top_contributors": [], "reason": "Недостаточно данных для root cause."}
    ranked = sorted(contributors, key=lambda item: float(item.get("impact") or 0), reverse=True)[:3]
    return {"status": "ROOT_CAUSE_FOUND" if ranked else "ROOT_CAUSE_NOT_PROVEN", "top_contributors": ranked, "reason": None if ranked else "Доказанный contributor отсутствует."}


def finalize_decision_pack(pack: dict[str, Any], findings: dict[str, Any]) -> dict[str, Any]:
    """Attach only deterministic, evidence-backed contributors after findings exist."""
    contributors = []
    for group in (findings.get("watch", []), findings.get("positive", [])):
        for item in group:
            refs = item.get("evidence_ids") or []
            if refs:
                contributors.append({
                    "dimension": item.get("dimension"),
                    "entity": item.get("entity") or item.get("label"),
                    "finding_type": item.get("finding_type"),
                    "impact": item.get("impact", 0),
                    "evidence_ids": refs,
                    "status": "OBSERVED",
                })
    sufficient = (
        pack.get("metrics", {}).get("status") == "READY"
        and bool(pack.get("metrics", {}).get("current", {}).get("clicks"))
    )
    pack["root_cause"] = root_cause_status(contributors=contributors, sufficient=sufficient)
    pack["confidence"] = confidence(
        clicks=pack.get("metrics", {}).get("current", {}).get("clicks"),
        confirmed_outcomes=pack.get("metrics", {}).get("current", {}).get("confirmed_outcomes"),
        freshness_hours=None,
        attribution_coverage=pack.get("outcomes", {}).get("attribution_coverage"),
        root_cause_proven=pack["root_cause"]["status"] == "ROOT_CAUSE_FOUND",
        windows_ready=pack.get("metrics", {}).get("status") == "READY",
    )
    pack["data_health"] = {
        "status": "LIMITED" if pack["confidence"]["level"] in {"LOW", "INSUFFICIENT"} else "HEALTHY",
        "reasons": pack["confidence"]["reasons"],
    }
    return pack


async def build_decision_pack(db: AsyncSession, *, campaign: AdsCampaign, daily_rows: list[Any], today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).astimezone(MOSCOW).date()
    start = None
    try:
        start = campaign.start_date
    except AttributeError:
        pass
    windows = comparison_windows(today=today, campaign_start=start)
    metrics = comparative_metrics(daily_rows, windows)
    current_start = datetime.fromisoformat(windows["current"]["start"]).replace(tzinfo=MOSCOW).astimezone(timezone.utc)
    current_end = datetime.fromisoformat(windows["current"]["end"]).replace(tzinfo=MOSCOW).astimezone(timezone.utc) + timedelta(days=1)
    outcomes = await confirmed_outcome_summary(db, campaign=campaign, window_start=current_start, window_end=current_end)
    current = metrics["current"]
    current["confirmed_outcomes"] = outcomes["attributed_outcomes"] if outcomes["object_type"] != "UNKNOWN" else None
    current["confirmed_cpa"] = current["cost"] / current["confirmed_outcomes"] if current.get("confirmed_outcomes") else None
    metrics["current"] = current
    metrics["confirmed_outcome_kpi"] = "max_subscriber" if outcomes["object_type"] == "CHANNEL" else "bot_started" if outcomes["object_type"] == "BOT" else "UNKNOWN"
    trend = classify_trend(current=current.get("provider_cpa"), baseline=metrics["baseline"].get("provider_cpa"), metric="provider_cpa", clicks=current.get("clicks"), confirmed_outcomes=current.get("confirmed_outcomes"))
    attribution = outcomes.get("attribution_coverage")
    confidence_pack = confidence(clicks=current.get("clicks"), confirmed_outcomes=current.get("confirmed_outcomes"), freshness_hours=None, attribution_coverage=attribution, root_cause_proven=False, windows_ready=metrics["status"] == "READY")
    return {
        "version": "directologist_v1",
        "object_type": outcomes["object_type"],
        "windows": windows,
        "metrics": metrics,
        "outcomes": {key: value for key, value in outcomes.items() if key != "rows"},
        "trend": {"state": trend, "metric": "provider_cpa", "source": "provider signal; not confirmed MaxiBot CPA"},
        "root_cause": root_cause_status(contributors=[], sufficient=False),
        "confidence": confidence_pack,
        "data_health": {"status": "LIMITED" if confidence_pack["level"] in {"LOW", "INSUFFICIENT"} else "HEALTHY", "reasons": confidence_pack["reasons"]},
        "retention_policy": {
            "observations_days": settings.ads_observations_retention_days,
            "daily_aggregates": "LONG_TERM",
            "detail_absence_semantics": ["NOT_COLLECTED", "EXPIRED_BY_RETENTION", "MISSING_OR_ERROR"],
        },
        "policy": "read-only; no Direct mutations; provider conversions are not confirmed MaxiBot outcomes",
        "evidence": {
            "V2-DIRECTOLOGISTCURRENT": {"source": "ADS_DIRECTOLOGIST", "scope": "current_window", "status": metrics["status"]},
            "V2-DIRECTOLOGISTBASELINE": {"source": "ADS_DIRECTOLOGIST", "scope": "baseline_window", "status": metrics["status"]},
            "V2-DIRECTOLOGISTOUTCOMES": {"source": outcomes["source_of_truth"], "scope": "confirmed_outcomes", "quality_counts": outcomes["quality_counts"]},
        },
    }
