"""Deterministic, bounded analysis dataset built only from ADS V2 rows."""
from __future__ import annotations

import copy
import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdsAnalysisSnapshot, AdsCampaign, AdsDailyAggregate, AdsObservation
from app.ads.facts import CampaignFactSet
from app.ads.directologist import build_decision_pack, finalize_decision_pack
from app.ads.trends import build_trend_features
from app.core.config import settings
import json

DIMENSIONS = ("AD_GROUP", "AD", "PLACEMENT", "REGION", "DEVICE", "DEMOGRAPHICS", "CRITERIA")
METRICS = ("impressions", "clicks", "cost", "conversions")
RECOMMENDATION_LADDER = ("NO_ACTION", "WATCH", "INVESTIGATE", "CONSIDER_CHANGE", "STRONG_RECOMMENDATION")
SIGNAL_EVOLUTION = ("NEW", "PERSISTENT", "STRENGTHENING", "WEAKENING", "RESOLVED", "INSUFFICIENT_NEW_DATA")


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


def classify_signal_evolution(
    finding: dict[str, Any],
    trends: list[dict[str, Any]] | None = None,
    previous_recommendations: list[dict[str, Any]] | None = None,
    measured_outcomes: list[dict[str, Any]] | None = None,
) -> str:
    """Classify one signal without counting trend/dimension rows as new signals."""
    if finding.get("sample_sufficiency") in {"INSUFFICIENT", "UNKNOWN", None}:
        return "INSUFFICIENT_NEW_DATA"
    dimension = str(finding.get("dimension") or "").upper()
    entity = str(finding.get("entity") or finding.get("label") or "")
    matching = [item for item in trends or [] if str(item.get("dimension") or "").upper() == dimension and str(item.get("entity") or item.get("label") or "") == entity]
    if matching:
        state = str(matching[0].get("trend_state") or "")
        if state in {"DEGRADING", "VOLATILE"}:
            return "STRENGTHENING" if abs(float(matching[0].get("relative_delta") or 0)) >= abs(float(finding.get("cpa_delta_pct") or 0)) else "PERSISTENT"
        if state in {"IMPROVING", "RECOVERED"}:
            return "WEAKENING" if finding.get("severity") == "WATCH" else "RESOLVED"
        if state == "STABLE":
            return "PERSISTENT"
    if any(str(item.get("status") or "").upper() == "COMPLETED" for item in measured_outcomes or []):
        return "PERSISTENT"
    if previous_recommendations:
        return "PERSISTENT"
    return "NEW"


def assess_recommendation_quality(
    finding: dict[str, Any],
    *,
    campaign_metrics: dict[str, Any] | None = None,
    trends: list[dict[str, Any]] | None = None,
    previous_recommendations: list[dict[str, Any]] | None = None,
    measured_outcomes: list[dict[str, Any]] | None = None,
    data_freshness_hours: float | None = None,
) -> dict[str, Any]:
    """Score a candidate for user value; this never executes an ad action."""
    sample = finding.get("sample_sufficiency") or finding.get("metrics", {}).get("sample_sufficiency")
    confidence = finding.get("confidence") or finding.get("metrics", {}).get("confidence")
    if sample in {"INSUFFICIENT", "UNKNOWN", None}:
        ladder = "NO_ACTION"
    elif sample != "SUFFICIENT":
        ladder = "INVESTIGATE"
    else:
        delta = abs(float(finding.get("cpa_delta_pct") or finding.get("metrics", {}).get("cpa_delta_pct") or 0))
        impact = float(finding.get("impact") or 0)
        evolution = classify_signal_evolution(finding, trends, previous_recommendations, measured_outcomes)
        if finding.get("severity") != "WATCH" and finding.get("materiality") != "HIGH":
            ladder = "WATCH"
        elif evolution in {"STRENGTHENING", "PERSISTENT"} and delta >= 0.5 and impact > 0:
            ladder = "STRONG_RECOMMENDATION"
        elif delta >= 0.2 or impact > 0:
            ladder = "CONSIDER_CHANGE"
        else:
            ladder = "WATCH"
    evolution = classify_signal_evolution(finding, trends, previous_recommendations, measured_outcomes)
    delta = abs(float(finding.get("cpa_delta_pct") or finding.get("metrics", {}).get("cpa_delta_pct") or 0))
    economic_impact = float(finding.get("impact") or 0)
    magnitude = min(1.0, delta)
    return {
        "action_ladder": ladder,
        "signal_evolution": evolution,
        "economic_impact": economic_impact,
        "sample_sufficiency": sample or "INSUFFICIENT",
        "magnitude": magnitude,
        "persistence": evolution in {"PERSISTENT", "STRENGTHENING"},
        "actionability": "HIGH" if sample == "SUFFICIENT" and finding.get("severity") == "WATCH" else "LOW",
        "reversibility": "HIGH",
        "risk": "LOW" if finding.get("severity") == "WATCH" else "UNKNOWN",
        "confidence": confidence or "UNKNOWN",
        "data_freshness_hours": data_freshness_hours,
        "why_this": "Сигнал основан на сравнении с итогом кампании и отобранных данных." if sample == "SUFFICIENT" else "Недостаточно данных для вывода.",
        "why_now": "Есть достаточная выборка для проверки." if sample == "SUFFICIENT" else "Нужно дождаться достаточных данных.",
        "evidence": list(finding.get("evidence_ids") or []),
        "risk_if_ignored": "Потенциально продолжатся неэффективные расходы." if ladder in {"CONSIDER_CHANGE", "STRONG_RECOMMENDATION"} else "Риск не подтверждён.",
        "next_step": "Проверить сигнал вручную; автоматические изменения отключены." if ladder not in {"NO_ACTION", "WATCH"} else "Наблюдать.",
        "wait_condition": "Нужны новые наблюдения при недостаточной выборке." if sample != "SUFFICIENT" else None,
    }


def build_recommendation_quality_layer(
    findings: dict[str, Any],
    *,
    campaign_metrics: dict[str, Any] | None = None,
    trends: list[dict[str, Any]] | None = None,
    previous_recommendations: list[dict[str, Any]] | None = None,
    measured_outcomes: list[dict[str, Any]] | None = None,
    data_freshness_hours: float | None = None,
) -> dict[str, Any]:
    """Select one primary and at most two distinct secondary recommendations."""
    candidates = list(findings.get("positive", [])) + list(findings.get("watch", []))
    ranked = sorted(candidates, key=_finding_rank)
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for finding in ranked:
        key = (finding.get("dimension"), finding.get("entity"), finding.get("finding_type"))
        if key in seen:
            continue
        seen.add(key)
        quality = assess_recommendation_quality(finding, campaign_metrics=campaign_metrics, trends=trends, previous_recommendations=previous_recommendations, measured_outcomes=measured_outcomes, data_freshness_hours=data_freshness_hours)
        selected.append({"finding_id": finding.get("finding_id"), "dimension": finding.get("dimension"), "entity": finding.get("entity"), "quality": quality})
        if len(selected) >= 3:
            break
    primary = selected[0] if selected else {"finding_id": None, "quality": {"action_ladder": "NO_ACTION", "signal_evolution": "INSUFFICIENT_NEW_DATA"}}
    outcome_rows = measured_outcomes or []
    outcome_state = "COMPLETED" if any(str(row.get("status", "")).upper() == "COMPLETED" for row in outcome_rows) else "PENDING" if outcome_rows else "UNKNOWN"
    history_state = "AVAILABLE" if previous_recommendations else "UNKNOWN"
    return {"primary": primary, "secondary": selected[1:3], "candidate_count": len(candidates), "history_state": history_state, "outcome_state": outcome_state, "dedup_policy": "one primary plus at most two distinct findings; trends are persistence evidence", "ladder": RECOMMENDATION_LADDER, "signal_evolution_values": SIGNAL_EVOLUTION}


def build_quality_decision_snapshot(
    dataset: dict[str, Any],
    *,
    quality: dict[str, Any] | None = None,
    history_state: str | None = None,
    outcome_state: str | None = None,
) -> dict[str, Any]:
    """Persist one bounded forensic view of the deterministic primary decision."""
    quality = quality or dataset.get("recommendation_quality") or {}
    primary = quality.get("primary") or {}
    finding_id = primary.get("finding_id")
    findings = list((dataset.get("findings") or {}).get("positive", [])) + list((dataset.get("findings") or {}).get("watch", []))
    finding = next((item for item in findings if item.get("finding_id") == finding_id), None) or {}
    decision = primary.get("quality") or {}
    metrics = finding.get("metrics") or {}
    safe_label = str(finding.get("label") or finding.get("entity") or "")
    if safe_label.count(".") >= 1:
        safe_label = safe_label.replace(".", "[.]")
    return {
        "version": "quality_decision_snapshot_v1",
        "primary_candidate": {
            "dimension": finding.get("dimension") or primary.get("dimension"),
            "safe_label": safe_label or None,
            "safe_entity": safe_label or None,
            "action_level": decision.get("action_ladder", "UNKNOWN"),
            "confidence": decision.get("confidence", "UNKNOWN"),
            "data_sufficiency": decision.get("sample_sufficiency", "UNKNOWN"),
            "economic_impact": decision.get("economic_impact"),
            "sample_metrics": {key: metrics.get(key) for key in ("impressions", "clicks", "cost", "conversions", "cpa", "cpc", "ctr")},
            "spend_share": metrics.get("cost_share"),
            "relative_deviation": finding.get("cpa_delta_pct") or metrics.get("cpa_delta_pct"),
            "persistence": decision.get("persistence", "UNKNOWN"),
            "signal_evolution": decision.get("signal_evolution", "UNKNOWN"),
            "actionability": decision.get("actionability", "UNKNOWN"),
            "action_risk": decision.get("risk", "UNKNOWN"),
            "WHY_THIS": decision.get("why_this"),
            "WHY_NOW": decision.get("why_now"),
            "NEXT_STEP": decision.get("next_step"),
            "WAIT_CONDITION": decision.get("wait_condition"),
        },
        "history_state": history_state or quality.get("history_state") or "UNKNOWN",
        "outcome_state": outcome_state or quality.get("outcome_state") or "UNKNOWN",
    }


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
                "label": row.get("label") or row.get("value"),
                "campaign_cpa": campaign.get("cpa"),
                "evidence_ids": [row.get("evidence_id")],
                # Findings are a second view of selected rows, not a second
                # copy of the complete observation payload.
                "metrics": {key: row.get(key) for key in (
                    "impressions", "clicks", "cost", "conversions", "ctr", "cpc", "cpa", "cost_share", "conversion_share",
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


def dedupe_trend_findings(findings: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Collapse equivalent campaign/child trend rows without hiding distinct children."""
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for finding in findings or []:
        key = (finding.get("metric"), finding.get("period"), finding.get("trend_state"), round(float(finding.get("relative_delta") or 0), 2))
        grouped.setdefault(key, []).append(finding)
    result: list[dict[str, Any]] = []
    for group in grouped.values():
        campaigns = [item for item in group if item.get("dimension") == "CAMPAIGN"]
        children = [item for item in group if item.get("dimension") in {"AD_GROUP", "AD"}]
        child_ids = {str(item.get("entity_id") or item.get("entity")) for item in children}
        if campaigns and children and len(child_ids) <= 2:
            result.append(campaigns[0])
        else:
            result.extend(group)
    return result


def _with_semantic_coverage(view: dict[str, Any], *, findings: list[dict[str, Any]], trends: list[dict[str, Any]]) -> dict[str, Any]:
    """Add explicit current-material, weak, and final-trend counts in memory."""
    result = copy.deepcopy(view)
    primary = result.get("primary_findings") or []
    secondary = result.get("secondary_findings") or []
    if not secondary:
        secondary = [item for item in findings if item not in primary]
    final_trends = dedupe_trend_findings(trends)
    for name, item in (result.get("comparison") or {}).items():
        dimension = str(name).upper()
        material = sum(1 for finding in primary if str(finding.get("dimension", "")).upper() == dimension)
        weak = sum(1 for finding in secondary if str(finding.get("dimension", "")).upper() == dimension and finding.get("sample_sufficiency") == "WEAK_SIGNAL")
        trend = sum(1 for finding in final_trends if str(finding.get("dimension", "")).upper() == dimension)
        item["material_findings"] = material
        item["weak_signals"] = weak
        item["trend_signals"] = trend
        item["material_findings_count"] = material
        item["weak_signals_count"] = weak
        item["trend_signals_count"] = trend
        item["available_count"] = item.get("available")
        item["selected_count"] = item.get("included")
    return result


def build_report_view(dataset: dict[str, Any]) -> dict[str, Any]:
    """Return one canonical report view, deriving legacy snapshots in memory only."""
    stored = dataset.get("report_view")
    if isinstance(stored, dict) and isinstance(stored.get("comparison"), dict):
        findings = list((dataset.get("findings") or {}).get("positive", [])) + list((dataset.get("findings") or {}).get("watch", []))
        view = _with_semantic_coverage(stored, findings=findings, trends=(dataset.get("trends") or {}).get("findings") or stored.get("trend_findings") or [])
        view.setdefault("recommendation_quality", dataset.get("recommendation_quality") or build_recommendation_quality_layer(dataset.get("findings") or {}, campaign_metrics=(dataset.get("campaign_summary") or {}).get("metrics"), trends=(dataset.get("trends") or {}).get("findings")))
        return view
    findings = dataset.get("findings") or {}
    candidates = list(findings.get("positive", [])) + list(findings.get("watch", []))
    campaign_metrics = (dataset.get("campaign_summary") or {}).get("metrics") or {}
    normalized: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        copy.setdefault("label", copy.get("entity"))
        copy.setdefault("campaign_cpa", campaign_metrics.get("cpa"))
        copy.setdefault("metrics", {})
        normalized.append(copy)
    primary = [item for item in normalized if item.get("materiality") == "HIGH" and item.get("sample_sufficiency") == "SUFFICIENT"]
    selection = dataset.get("selection") or {}
    sections = dataset.get("sections") or {}
    comparison: dict[str, dict[str, Any]] = {}
    for dimension in DIMENSIONS:
        name = dimension.lower()
        selected = selection.get(name) if isinstance(selection.get(name), dict) else None
        section = sections.get(name) if isinstance(sections.get(name), dict) else None
        available = selected.get("rows_available") if selected and "rows_available" in selected else None
        included = selected.get("rows_included") if selected and "rows_included" in selected else None
        if available is None and section is not None and "total_rows_analyzed" in section:
            available = section.get("total_rows_analyzed")
        if included is None and section is not None and "notable" in section:
            included = len(section.get("notable") or [])
        material = sum(1 for item in normalized if str(item.get("dimension", "")).upper() == dimension and item.get("materiality") == "HIGH")
        state = "UNKNOWN" if available is None else ("PROVIDER_UNAVAILABLE" if not available else "SINGLE_ENTITY" if available == 1 else "AVAILABLE" if material else "NO_MATERIAL_DIFFERENCE")
        comparison[name] = {"available": available, "included": included, "material_findings": material, "state": state}
    trends = dataset.get("trends") or {}
    view = {
        "primary_findings": sorted(primary, key=lambda item: (-float(item.get("impact", 0.0)), str(item.get("label") or item.get("entity") or "")))[:5],
        "secondary_findings": [item for item in normalized if item not in primary][:5],
        "trend_findings": dedupe_trend_findings(trends.get("findings")),
        "comparison": comparison,
        "creative_comparison": comparison.get("ad", {}).get("state", "UNKNOWN"),
        "recommendation_quality": dataset.get("recommendation_quality") or build_recommendation_quality_layer(findings, campaign_metrics=campaign_metrics, trends=trends.get("findings") or []),
    }
    return _with_semantic_coverage(view, findings=normalized, trends=trends.get("findings") or [])


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
        referenced.update((data.get("DIRECTOLOGIST") or {}).get("evidence", {}).keys())
        data["evidence"] = {key: value for key, value in data.get("evidence", {}).items() if key in referenced}
    def sync_report_view() -> None:
        view = data.get("report_view")
        if not view:
            return
        findings = data.get("findings") or {}
        available_findings = findings.get("positive", []) + findings.get("watch", [])
        view["primary_findings"] = [
            item for item in view.get("primary_findings", [])
            if item in available_findings and all(ref in data.get("evidence", {}) for ref in item.get("evidence_ids", []))
        ]
        for name, item in (view.get("comparison") or {}).items():
            selection = data.get("selection", {}).get(name, {})
            item["included"] = int(selection.get("rows_included", item.get("included", 0)))
            item["material_findings"] = sum(1 for finding in view.get("primary_findings", []) if str(finding.get("dimension", "")).lower() == name)
            item["weak_signals"] = sum(1 for finding in view.get("secondary_findings", []) if str(finding.get("dimension", "")).lower() == name and finding.get("sample_sufficiency") == "WEAK_SIGNAL")
            item["trend_signals"] = sum(1 for finding in view.get("trend_findings", []) if str(finding.get("dimension", "")).lower() == name)
            item["material_findings_count"] = item["material_findings"]
            item["weak_signals_count"] = item["weak_signals"]
            item["trend_signals_count"] = item["trend_signals"]
            item["available_count"] = item.get("available")
            item["selected_count"] = item.get("included")
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
        sync_report_view()
    data["context_budget"] = {"max_bytes": max_bytes, "max_evidence": max_evidence, "bytes": len(json.dumps(data, ensure_ascii=False).encode("utf-8")), "evidence": len(data.get("evidence", {})), "budget_exceeded": len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > max_bytes}
    sync_report_view()
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


async def build_v2_analysis_dataset(
    db: AsyncSession,
    *,
    campaign: AdsCampaign,
    cutoff: date | None = None,
    top_n: int = 10,
    previous_recommendations: list[dict[str, Any]] | None = None,
    measured_outcomes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cutoff = cutoff or (datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow")).date() - timedelta(days=6))
    rows = list((await db.scalars(
        select(AdsObservation)
        .join(AdsAnalysisSnapshot, AdsAnalysisSnapshot.id == AdsObservation.snapshot_id)
        .where(AdsAnalysisSnapshot.campaign_id == campaign.id, AdsObservation.date_to >= cutoff)
        .order_by(AdsObservation.created_at.desc())
    )).all())
    latest: dict[tuple[Any, ...], AdsObservation] = {}
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
            item = {"value": value, "label": value, **_metrics(values, campaign=campaign_summary, history_days=7)}
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
    daily_rows = list((await db.scalars(select(AdsDailyAggregate).where(AdsDailyAggregate.campaign_id == campaign.id).order_by(AdsDailyAggregate.aggregate_date, AdsDailyAggregate.dimension_type))).all())
    decision_pack = await build_decision_pack(db, campaign=campaign, daily_rows=daily_rows)
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
        "dataset_version": "ads_canonical_analysis_v2",
        "campaign_id": campaign.id,
        "period": {"from": cutoff.isoformat(), "to": max((r.date_to for r in latest.values() if r.date_to), default=cutoff).isoformat(), "timezone": "Europe/Moscow"},
        "data_cutoff": datetime.now(timezone.utc).isoformat(),
        "campaign_summary": {"windows": campaign_windows, "metrics": campaign_metrics},
        "sections": sections,
        "selection": selection,
        "data_quality": {"rows_loaded": len(rows), "rows_after_dedupe": len(latest), "history_days": len({r.date_to for r in latest.values() if r.date_to}), "insufficient_history_for_trend": len({r.date_to for r in latest.values() if r.date_to}) < 3, "missing_dimensions": [dimension for dimension in DIMENSIONS if not grouped[dimension]], "source": "ADS_DATA_LAYER_V2"},
        "provenance_summary": {"provider": "YANDEX_DIRECT", "normalized_only": True, "observation_rows_analyzed": len(latest)},
        "DIRECTOLOGIST": decision_pack,
        "evidence": {**evidence, **trend_evidence},
        "trends": trends,
    }
    dataset["findings"] = build_deterministic_findings(dataset)
    dataset["DIRECTOLOGIST"] = finalize_decision_pack(dataset["DIRECTOLOGIST"], dataset["findings"])
    dataset["evidence"].update(dataset["DIRECTOLOGIST"].get("evidence", {}))
    dataset["recommendation_quality"] = build_recommendation_quality_layer(
        dataset["findings"],
        campaign_metrics=campaign_metrics,
        trends=dataset.get("trends", {}).get("findings", []),
        previous_recommendations=previous_recommendations,
        measured_outcomes=measured_outcomes,
    )
    dataset["QUALITY_DECISION_SNAPSHOT"] = build_quality_decision_snapshot(dataset)
    comparison = {}
    finding_items = dataset["findings"].get("positive", []) + dataset["findings"].get("watch", [])
    for dimension in DIMENSIONS:
        name = dimension.lower()
        available = int(dataset["selection"].get(name, {}).get("rows_available", 0))
        included = int(dataset["selection"].get(name, {}).get("rows_included", 0))
        material = sum(1 for item in finding_items if item.get("dimension") == dimension)
        state = ("PROVIDER_UNAVAILABLE" if not available else "SINGLE_ENTITY" if available == 1
                 else "AVAILABLE" if material else "NO_MATERIAL_DIFFERENCE")
        comparison[name] = {"available": available, "included": included, "material_findings": material, "state": state}
    dataset["report_view"] = build_report_view({**dataset, "findings": dataset["findings"], "comparison": comparison})
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
