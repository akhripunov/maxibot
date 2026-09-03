"""Deterministic, bounded analysis dataset built only from ADS V2 rows."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsCampaign, AdsSnapshot, AdsV2DailyAggregate, AdsV2Observation
from app.ads.facts import CampaignFactSet
from app.ads.trends import build_trend_features
from app.core.config import settings
import json

DIMENSIONS = ("AD_GROUP", "AD", "PLACEMENT", "REGION", "DEVICE", "DEMOGRAPHICS", "CRITERIA")
METRICS = ("impressions", "clicks", "cost", "conversions")


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _confidence(row: dict[str, Any], *, history_days: int) -> str:
    if history_days < 1 or (row["impressions"] == 0 and row["clicks"] == 0 and row["cost"] == 0):
        return "INSUFFICIENT_DATA"
    if row["impressions"] >= 100 and row["clicks"] >= 10 and row["conversions"] >= 3:
        return "HIGH"
    if row["impressions"] >= 30 and row["clicks"] >= 3:
        return "MEDIUM"
    return "LOW"


def _metrics(values: dict[str, float], *, campaign: dict[str, float], history_days: int) -> dict[str, Any]:
    row = {key: float(values.get(key, 0.0)) for key in METRICS}
    row["ctr"] = row["clicks"] / row["impressions"] if row["impressions"] else None
    row["cpc"] = row["cost"] / row["clicks"] if row["clicks"] else None
    row["cvr"] = row["conversions"] / row["clicks"] if row["clicks"] else None
    row["cpa"] = row["cost"] / row["conversions"] if row["conversions"] else None
    row["cost_share"] = row["cost"] / campaign["cost"] if campaign["cost"] else None
    row["conversion_share"] = row["conversions"] / campaign["conversions"] if campaign["conversions"] else None
    row["confidence"] = _confidence(row, history_days=history_days)
    row["sample_sufficiency"] = (
        "SUFFICIENT" if row["confidence"] == "HIGH"
        else "WEAK_SIGNAL" if row["confidence"] in {"MEDIUM", "LOW"}
        else "INSUFFICIENT"
    )
    for metric in ("ctr", "cpc", "cvr", "cpa"):
        baseline = _metrics_baseline_metric(metric, campaign)
        value = row.get(metric)
        row[f"{metric}_delta_pct"] = ((value / baseline) - 1) if value is not None and baseline not in (None, 0) else None
    return row


def _metrics_baseline_metric(metric: str, campaign: dict[str, float]) -> float | None:
    impressions = campaign.get("impressions", 0.0)
    clicks = campaign.get("clicks", 0.0)
    cost = campaign.get("cost", 0.0)
    conversions = campaign.get("conversions", 0.0)
    return {
        "ctr": clicks / impressions if impressions else None,
        "cpc": cost / clicks if clicks else None,
        "cvr": conversions / clicks if clicks else None,
        "cpa": cost / conversions if conversions else None,
    }[metric]


def _finding_rank(item: dict[str, Any]) -> tuple[float, int, float, str]:
    return (-float(item.get("impact", 0.0)), 0 if item.get("sample_sufficiency") == "SUFFICIENT" else 1, -float(item.get("cost", 0.0)), str(item.get("entity", "")))


def build_deterministic_findings(dataset: dict[str, Any]) -> dict[str, Any]:
    """Create compact, evidence-backed candidates before the LLM sees V2 rows."""
    campaign = (dataset.get("campaign_summary") or {}).get("metrics") or {}
    candidates: list[dict[str, Any]] = []
    for dimension, section in (dataset.get("sections") or {}).items():
        for row in section.get("notable", []):
            baseline = row.get("cpa_delta_pct")
            if row.get("sample_sufficiency") == "INSUFFICIENT":
                continue
            common = {
                "dimension": dimension.upper(), "entity": row.get("value"),
                "evidence_ids": [row.get("evidence_id")],
                # Findings are a second view of selected rows, not a second
                # copy of the complete observation payload.
                "metrics": {key: row.get(key) for key in (
                    "cost", "conversions", "cpa", "cost_share", "conversion_share",
                    "confidence", "sample_sufficiency",
                )},
                "sample_sufficiency": row.get("sample_sufficiency"),
                "cost": row.get("cost", 0.0), "materiality": "LOW", "impact": 0.0,
            }
            if row.get("sample_sufficiency") == "SUFFICIENT" and row.get("cpa") is not None and baseline is not None and baseline >= 0.5 and (row.get("cost_share") or 0) >= 0.05:
                common.update({"finding_id": f"{dimension.upper()}_HIGH_CPA", "finding_type": "HIGH_CPA", "severity": "WATCH", "materiality": "HIGH", "impact": abs(baseline) * float(row.get("cost", 0.0))})
                candidates.append(common)
            elif row.get("sample_sufficiency") == "SUFFICIENT" and row.get("cpa") is not None and baseline is not None and baseline <= -0.2 and row.get("conversions", 0) >= 3:
                common.update({"finding_id": f"{dimension.upper()}_STRONG_CPA", "finding_type": "STRONG_PERFORMANCE", "severity": "POSITIVE", "materiality": "HIGH", "impact": abs(baseline) * float(row.get("cost", 0.0))})
                candidates.append(common)
            elif row.get("sample_sufficiency") == "WEAK_SIGNAL" and ((baseline is not None and abs(baseline) >= 0.5) or row.get("cost_share", 0) >= 0.1):
                common.update({"finding_id": f"{dimension.upper()}_WATCH_SIGNAL", "finding_type": "WATCH_SIGNAL", "severity": "WATCH", "materiality": "MEDIUM", "impact": abs(baseline or 0.0) * float(row.get("cost", 0.0))})
                candidates.append(common)
    positives = sorted((x for x in candidates if x["severity"] == "POSITIVE"), key=_finding_rank)[:5]
    watch = sorted((x for x in candidates if x["severity"] == "WATCH"), key=_finding_rank)[:5]
    return {
        "engine_version": "deterministic_findings_v1",
        "all_candidates": len(candidates),
        "positive": positives,
        "watch": watch,
        "insufficient_rows": sum(1 for section in (dataset.get("sections") or {}).values() for row in section.get("notable", []) if row.get("sample_sufficiency") == "INSUFFICIENT"),
        "limits": {"positive": 5, "watch": 5, "data_health": 3},
        "policy": "recommendations are read-only; no automatic exclusion or campaign changes",
    }


def _evidence_id(dimension: str, value: str) -> str:
    return "V2-" + hashlib.sha256(f"{dimension}:{value}".encode()).hexdigest()[:12]


def bound_v2_dataset(dataset: dict[str, Any], *, max_bytes: int, max_evidence: int) -> dict[str, Any]:
    """Deterministically trim only the AI payload; never mutates stored V2 rows."""
    data = json.loads(json.dumps(dataset, ensure_ascii=False))
    order = ["criteria", "demographics", "device", "region", "placement", "ad", "ad_group"]
    def sync_evidence() -> None:
        referenced = {
            item.get("evidence_id")
            for section in data.get("sections", {}).values()
            for item in section.get("notable", [])
            if item.get("evidence_id")
        }
        findings = data.get("findings") or {}
        for group in ("positive", "watch"):
            for item in findings.get(group, []):
                referenced.update(item.get("evidence_ids") or [])
        for item in (data.get("trends") or {}).get("findings", []):
            referenced.update(item.get("evidence_ids") or [])
        data["evidence"] = {key: value for key, value in data.get("evidence", {}).items() if key in referenced}
    sync_evidence()
    def trim_one() -> bool:
        for name in order:
            items = data.get("sections", {}).get(name, {}).get("notable", [])
            if items:
                item = items.pop()
                evidence_id = item.get("evidence_id")
                if evidence_id:
                    data.get("evidence", {}).pop(evidence_id, None)
                data.get("selection", {}).get(name, {})["rows_included"] = len(items)
                data.get("selection", {}).get(name, {})["rows_excluded"] = max(0, data.get("selection", {}).get(name, {}).get("rows_available", len(items)) - len(items))
                data.get("selection", {}).get(name, {})["truncated"] = True
                findings = data.get("findings") or {}
                for group in ("positive", "watch"):
                    findings[group] = [item for item in findings.get(group, []) if all(ref in data.get("evidence", {}) for ref in item.get("evidence_ids", []))]
                return True
        return False
    while len(data.get("evidence", {})) > max_evidence or len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > max_bytes:
        if not trim_one():
            break
        sync_evidence()
    data["context_budget"] = {"max_bytes": max_bytes, "max_evidence": max_evidence, "bytes": len(json.dumps(data, ensure_ascii=False).encode("utf-8")), "evidence": len(data.get("evidence", {})), "budget_exceeded": len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > max_bytes}
    return data


def _compact_trends(trends: dict[str, Any], *, max_findings: int = 8) -> dict[str, Any]:
    """Return trend features for AI without serializing daily history."""
    metric_priority = {
        "cpa": 0, "conversions": 1, "cvr": 1, "cost": 2,
        "cpc": 3, "clicks": 4, "ctr": 5, "impressions": 6,
    }
    findings = sorted(
        trends.get("findings") or [],
        key=lambda item: (
            metric_priority.get(str(item.get("metric")), 99),
            -int(item.get("materiality") == "MEDIUM"),
            str(item.get("dimension")), str(item.get("entity")),
        ),
    )[:max_findings]
    compact_findings = []
    for item in findings:
        compact_findings.append({
            key: item.get(key) for key in (
                "trend_finding_id", "dimension", "entity", "entity_id", "metric",
                "period", "trend_state", "current_value", "previous_value",
                "absolute_delta", "relative_delta", "sample_sufficiency",
                "materiality", "evidence_ids", "trend_engine_version",
            )
        })
    completed = trends.get("completed_days") or []
    return {
        "trend_engine_version": trends.get("trend_engine_version"),
        "history_days_available": trends.get("history_days_available", 0),
        "completed_days_count": len(completed),
        "completed_date_from": min(completed) if completed else None,
        "completed_date_to": max(completed) if completed else None,
        "current_day_partial": bool(trends.get("current_day_partial")),
        "history_health": trends.get("history_health"),
        "trend_7d_vs_previous_7d": (
            "OK" if trends.get("history_days_available", 0) >= 14
            else "INSUFFICIENT_HISTORY"
        ),
        "required_days_for_7d": 14,
        "findings": compact_findings,
        "counts": trends.get("counts") or {},
        "policy": trends.get("policy"),
    }


async def build_v2_analysis_dataset(db: AsyncSession, *, campaign: AdsCampaign, cutoff: date | None = None, top_n: int = 10) -> dict[str, Any]:
    cutoff = cutoff or (datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow")).date() - timedelta(days=6))
    rows = list((await db.scalars(
        select(AdsV2Observation)
        .join(AdsSnapshot, AdsSnapshot.id == AdsV2Observation.snapshot_id)
        .where(AdsSnapshot.campaign_id == campaign.id, AdsV2Observation.date_to >= cutoff)
        .order_by(AdsV2Observation.created_at.desc())
    )).all())
    latest: dict[tuple[Any, ...], AdsV2Observation] = {}
    for row in rows:
        key = (row.dimension_type, row.entity_id, row.dimension_value, row.date_from, row.date_to, row.window)
        latest.setdefault(key, row)
    campaign_windows: dict[str, dict[str, float]] = {}
    for row in latest.values():
        if row.dimension_type == "CAMPAIGN":
            campaign_windows.setdefault(row.window, {metric: _number(getattr(row, metric)) for metric in METRICS})
    campaign_summary = campaign_windows.get("7d") or campaign_windows.get("3d") or campaign_windows.get("1d")
    campaign_summary = campaign_summary or {metric: 0.0 for metric in METRICS}
    campaign_metrics = _metrics(campaign_summary, campaign=campaign_summary, history_days=max(0, (max((r.date_to for r in latest.values() if r.date_to), default=cutoff) - cutoff).days + 1))
    grouped: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(lambda: {metric: 0.0 for metric in METRICS}))
    for row in latest.values():
        if row.dimension_type not in DIMENSIONS or not row.dimension_value:
            continue
        target = grouped[row.dimension_type][str(row.dimension_value)]
        for metric in METRICS:
            target[metric] += _number(getattr(row, metric))
    sections: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    limits = {
        "AD_GROUP": settings.ads_ai_max_adgroup_facts,
        "AD": settings.ads_ai_max_ad_facts,
        "PLACEMENT": settings.ads_ai_max_placement_facts,
        "REGION": settings.ads_ai_max_region_facts,
        "DEVICE": settings.ads_ai_max_device_facts,
        "DEMOGRAPHICS": settings.ads_ai_max_demographic_facts,
        "CRITERIA": settings.ads_ai_max_criteria_facts,
    }
    selection = {}
    for dimension in DIMENSIONS:
        calculated = []
        for value, values in grouped[dimension].items():
            item = {"value": value, **_metrics(values, campaign=campaign_summary, history_days=7)}
            item["evidence_id"] = _evidence_id(dimension, value)
            evidence[item["evidence_id"]] = {
                "source": "ADS_V2_OBSERVATION",
                "dimension": dimension,
                "value": value,
                "scope": "selected_ai_observation",
            }
            calculated.append(item)
        calculated.sort(key=lambda item: item["cost"], reverse=True)
        # A row is eligible for comparative conclusions only with a meaningful
        # sample. All rows remain in V2 storage; this gate only affects AI input.
        eligible = [item for item in calculated if item["impressions"] >= 30 or item["clicks"] >= 3 or item["conversions"] >= 1]
        limit = max(1, limits[dimension])
        selected: dict[str, dict[str, Any]] = {}
        ranked = [
            sorted(eligible, key=lambda item: (-item["cost"], str(item["value"]))),
            sorted(eligible, key=lambda item: (-item["conversions"], -item["cost"], str(item["value"]))),
            sorted([item for item in eligible if item["cpa"] is not None], key=lambda item: (-item["cpa"], str(item["value"]))),
            sorted([item for item in eligible if item["cpa"] is not None], key=lambda item: (item["cpa"], str(item["value"]))),
        ]
        for group in ranked:
            for item in group:
                selected.setdefault(str(item["value"]), item)
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break
        notable = list(selected.values())
        notable.sort(key=lambda item: (-item["cost"], str(item["value"])))
        for item in notable:
            evidence[item["evidence_id"]] = {
                "source": "ADS_V2_OBSERVATION",
                "dimension": dimension,
                "value": item["value"],
                "scope": "selected_ai_observation",
            }
        sections[dimension.lower()] = {"total_rows_analyzed": len(calculated), "notable": notable}
        selection[dimension.lower()] = {
            "rows_available": len(calculated), "rows_eligible": len(eligible),
            "rows_included": len(notable), "rows_excluded": max(0, len(calculated) - len(notable)),
            "truncated": len(notable) < len(calculated), "selection_method": "deterministic_multi_rank_v1",
            "sufficiency_gate_version": "impressions>=30 OR clicks>=3 OR conversions>=1",
        }
    daily_rows = list((await db.scalars(select(AdsV2DailyAggregate).where(AdsV2DailyAggregate.campaign_id == campaign.id).order_by(AdsV2DailyAggregate.aggregate_date, AdsV2DailyAggregate.dimension_type))).all())
    trend_rows = []
    trend_evidence: dict[str, Any] = {}
    for aggregate in daily_rows:
        evidence_id = "V2-" + hashlib.sha256(f"daily|{aggregate.id}|{aggregate.aggregate_date}".encode()).hexdigest()[:12]
        trend_rows.append({
            "window": "1d", "date_from": aggregate.aggregate_date, "date_to": aggregate.aggregate_date,
            "dimension_type": aggregate.dimension_type, "entity_id": aggregate.entity_id,
            "dimension_value": aggregate.dimension_value, "impressions": _number(aggregate.impressions),
            "clicks": _number(aggregate.clicks), "cost": _number(aggregate.cost), "conversions": _number(aggregate.conversions),
            "evidence_ids": [evidence_id],
        })
        trend_evidence[evidence_id] = {
            "source": "ADS_V2_DAILY_AGGREGATE",
            "aggregate_id": aggregate.id,
            "dimension": aggregate.dimension_type,
            "entity": aggregate.dimension_value,
            "date": aggregate.aggregate_date.isoformat(),
            # Full provenance remains in ADS Data Layer V2.  The AI payload
            # carries only a stable pointer and date, avoiding daily-row
            # duplication while retaining strict evidence membership.
        }
    # Let the AI-facing compactor choose by business priority.  Applying the
    # legacy engine's small output limit first could discard campaign CPA
    # before the compactor had a chance to rank it.
    trends = build_trend_features(
        trend_rows,
        current_day=datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow")).date(),
        max_findings=2000,
    )
    trends = _compact_trends(trends)
    referenced_trend_evidence = {
        evidence_id
        for finding in trends["findings"]
        for evidence_id in finding.get("evidence_ids", [])
    }
    trend_evidence = {
        key: value for key, value in trend_evidence.items()
        if key in referenced_trend_evidence
    }
    dataset = {
        "dataset_version": "ads_v2_analysis_v1",
        "campaign_id": campaign.id,
        "period": {"from": cutoff.isoformat(), "to": max((r.date_to for r in latest.values() if r.date_to), default=cutoff).isoformat(), "timezone": "Europe/Moscow"},
        "data_cutoff": datetime.now(timezone.utc).isoformat(),
        "campaign_summary": {"windows": campaign_windows, "metrics": campaign_metrics},
        "sections": sections,
        "selection": selection,
        "data_quality": {"rows_loaded": len(rows), "rows_after_dedupe": len(latest), "history_days": len({r.date_to for r in latest.values() if r.date_to}), "insufficient_history_for_trend": len({r.date_to for r in latest.values() if r.date_to}) < 3, "missing_dimensions": [dimension for dimension in DIMENSIONS if not grouped[dimension]], "source": "ADS_DATA_LAYER_V2"},
        "provenance_summary": {"provider": "YANDEX_DIRECT", "normalized_only": True, "observation_rows_analyzed": len(latest)},
        "evidence": {**evidence, **trend_evidence},
        "trends": trends,
    }
    dataset["findings"] = build_deterministic_findings(dataset)
    return bound_v2_dataset(dataset, max_bytes=max(12000, settings.ads_ai_context_max_bytes // 2), max_evidence=settings.ads_ai_evidence_max_entries)


def campaign_fact_set_from_v2(dataset: dict[str, Any], *, traffic: dict[str, Any], campaign_start: str | None = None) -> CampaignFactSet:
    windows = dataset["campaign_summary"]["windows"]
    def value(metric: str, window: str) -> float | None:
        item = windows.get(window)
        return item.get(metric) if item else None
    i7, c7, cost7, conv7 = (value(metric, "7d") for metric in ("impressions", "clicks", "cost", "conversions"))
    ratio = lambda numerator, denominator: None if numerator is None or denominator in (None, 0) else numerator / denominator
    end = date.fromisoformat(dataset["period"]["to"])
    age = None
    try:
        age = max(0, (end - date.fromisoformat(str(campaign_start))).days) if campaign_start else None
    except ValueError:
        pass
    visits, exact, high = traffic.get("visits"), traffic.get("EXACT"), traffic.get("HIGH")
    qualified = int(exact) + int(high) if exact is not None and high is not None else None
    return CampaignFactSet(
        campaign_age_days=age,
        impressions_1d=value("impressions", "1d"), impressions_3d=value("impressions", "3d"), impressions_7d=i7,
        clicks_1d=value("clicks", "1d"), clicks_3d=value("clicks", "3d"), clicks_7d=c7,
        cost_1d=value("cost", "1d"), cost_3d=value("cost", "3d"), cost_7d=cost7,
        conversions_1d=value("conversions", "1d"), conversions_3d=value("conversions", "3d"), conversions_7d=conv7,
        ctr_7d=ratio(c7, i7), cpc_7d=ratio(cost7, c7), conversion_rate_7d=ratio(conv7, c7), actual_cpa_7d=ratio(cost7, conv7),
        impressions_change_3d_vs_previous_3d=None, clicks_change_3d_vs_previous_3d=None, cost_change_3d_vs_previous_3d=None, conversions_change_3d_vs_previous_3d=None,
        landing_visits=int(visits) if visits is not None else None, max_redirects=int(traffic["to_channel"]) if traffic.get("to_channel") is not None else None,
        subscriptions=int(traffic["new_members"]) if traffic.get("new_members") is not None else None, qualified_subscriptions=qualified,
        maxibot_qualified_membership_rate=ratio(float(qualified), float(visits)) if qualified is not None and visits is not None else None,
        data_freshness_hours=None if not windows else 0.0, conversion_sample=conv7,
    )
