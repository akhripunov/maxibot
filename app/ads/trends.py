"""Deterministic Trend Intelligence V1 over canonical daily aggregates."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, timedelta
from math import sqrt
from typing import Any, Iterable

METRICS = ("impressions", "clicks", "cost", "conversions", "ctr", "cpc", "cvr", "cpa")
TREND_ENGINE_VERSION = "trend_intelligence_v1"
STABILITY_BAND = 0.05


def _metric(values: dict[str, float], metric: str) -> float | None:
    impressions, clicks, cost, conversions = (values.get(name, 0.0) for name in ("impressions", "clicks", "cost", "conversions"))
    return {
        "impressions": impressions, "clicks": clicks, "cost": cost, "conversions": conversions,
        "ctr": clicks / impressions if impressions else None,
        "cpc": cost / clicks if clicks else None,
        "cvr": conversions / clicks if clicks else None,
        "cpa": cost / conversions if conversions else None,
    }[metric]


def _relative(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous


def _state(metric: str, current: float | None, previous: float | None, series: list[float]) -> str:
    if current is None or previous is None:
        return "INSUFFICIENT_HISTORY"
    relative = _relative(current, previous)
    if relative is None:
        return "INSUFFICIENT_HISTORY"
    if len(series) >= 3:
        mean = sum(series) / len(series)
        if mean and sqrt(sum((value - mean) ** 2 for value in series) / len(series)) / mean > 0.35:
            return "VOLATILE"
        directions = [1 if b > a else -1 if b < a else 0 for a, b in zip(series, series[1:])]
        if sum(1 for a, b in zip(directions, directions[1:]) if a and b and a != b) >= 2:
            return "VOLATILE"
    if abs(relative) <= STABILITY_BAND:
        return "STABLE"
    improving = relative < 0 if metric in {"cpa", "cpc"} else relative > 0
    return "IMPROVING" if improving else "DEGRADING"


def _sample_sufficiency(values: dict[str, float], metric: str) -> str:
    if values.get("clicks", 0) < 10:
        return "INSUFFICIENT_SAMPLE"
    if metric in {"cpa", "cvr", "conversions"} and values.get("conversions", 0) < 3:
        return "INSUFFICIENT_SAMPLE"
    if metric in {"cpa", "cpc"} and values.get("cost", 0) <= 0:
        return "INSUFFICIENT_SAMPLE"
    return "SUFFICIENT"


def _period(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    selected = [row for row in rows if start <= row["date"] <= end]
    values = {metric: sum(float(row.get(metric) or 0) for row in selected) for metric in ("impressions", "clicks", "cost", "conversions")}
    return {"values": values, "dates": [row["date"].isoformat() for row in selected], "evidence_ids": [x for row in selected for x in row.get("evidence_ids", [])]}


def build_trend_features(rows: Iterable[dict[str, Any]], *, current_day: date, periods: tuple[int, ...] = (1, 3, 7), max_findings: int = 13) -> dict[str, Any]:
    """Build reproducible trends from daily rows only.

    Rows with a rolling window or a non-day date range are deliberately
    ignored, preventing 1d/3d/7d overlap from being double-counted.
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("window") != "1d" or row.get("date_from") != row.get("date_to"):
            continue
        day = row.get("date_to")
        if isinstance(day, str):
            day = date.fromisoformat(day)
        if not isinstance(day, date) or day >= current_day:
            continue
        key = (str(row.get("dimension_type") or "CAMPAIGN"), str(row.get("entity_id") or ""), str(row.get("dimension_value") or ""))
        grouped[key].append({**row, "date": day})

    findings: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, Any]] = {}
    for (dimension, entity_id, entity), series_rows in sorted(grouped.items()):
        series_rows.sort(key=lambda item: item["date"])
        available = {row["date"] for row in series_rows}
        missing = []
        if available:
            cursor = min(available)
            while cursor <= max(available):
                if cursor not in available:
                    missing.append(cursor.isoformat())
                cursor += timedelta(days=1)
        coverage_key = f"{dimension}:{entity_id}:{entity}"
        coverage[coverage_key] = {"dimension": dimension, "entity": entity, "days_available": len(available), "missing_buckets": missing, "partial_current_day": False}
        for days in periods:
            current = _period(series_rows, current_day - timedelta(days=days), current_day - timedelta(days=1))
            previous = _period(series_rows, current_day - timedelta(days=days * 2), current_day - timedelta(days=days + 1))
            required = 2 if days == 1 else days * 2
            history_state = "OK" if len(set(current["dates"] + previous["dates"])) >= required and not set(current["dates"] + previous["dates"]) & set(missing) else "SHORT"
            if days == 1 and current["dates"] and not previous["dates"]:
                for metric in ("cpa", "cvr", "cpc", "conversions"):
                    current_value = _metric(current["values"], metric)
                    if current_value is None or _sample_sufficiency(current["values"], metric) != "SUFFICIENT":
                        continue
                    findings.append({
                        "trend_finding_id": "TREND-" + hashlib.sha256(f"{dimension}|{entity_id}|{entity}|{metric}|new".encode()).hexdigest()[:12],
                        "dimension": dimension, "entity": entity, "entity_id": entity_id, "metric": metric,
                        "period": "24h_new_signal", "trend_state": "NEW_SIGNAL", "current_value": current_value,
                        "previous_value": None, "absolute_delta": None, "relative_delta": None,
                        "sample_sufficiency": "SUFFICIENT", "materiality": "LOW",
                        "evidence_ids": current["evidence_ids"], "trend_engine_version": TREND_ENGINE_VERSION,
                        "current_days": current["dates"], "previous_days": [],
                    })
                continue
            if history_state != "OK":
                continue
            for metric in ("impressions", "clicks", "cost", "ctr", "cpa", "cvr", "cpc", "conversions"):
                current_value = _metric(current["values"], metric)
                previous_value = _metric(previous["values"], metric)
                sample = _sample_sufficiency(current["values"], metric)
                if sample != "SUFFICIENT":
                    continue
                all_values = [_metric(_period(series_rows, day, day)["values"], metric) for day in sorted(available)]
                values = [float(value) for value in all_values if value is not None]
                state = _state(metric, current_value, previous_value, values)
                if state == "INSUFFICIENT_HISTORY":
                    continue
                if len(values) >= 3:
                    worsening = (values[-2] > values[-3]) if metric in {"cpa", "cpc"} else (values[-2] < values[-3])
                    improving = (values[-1] < values[-2]) if metric in {"cpa", "cpc"} else (values[-1] > values[-2])
                    if worsening and improving and state == "IMPROVING":
                        state = "RECOVERED"
                evidence_ids = list(dict.fromkeys(current["evidence_ids"] + previous["evidence_ids"]))
                findings.append({
                    "trend_finding_id": "TREND-" + hashlib.sha256(f"{dimension}|{entity_id}|{entity}|{metric}|{days}d".encode()).hexdigest()[:12],
                    "dimension": dimension, "entity": entity, "entity_id": entity_id, "metric": metric,
                    "period": f"{days}d_vs_previous_{days}d", "trend_state": state,
                    "current_value": current_value, "previous_value": previous_value,
                    "absolute_delta": current_value - previous_value if current_value is not None and previous_value is not None else None,
                    "relative_delta": _relative(current_value, previous_value),
                    "sample_sufficiency": sample, "materiality": "MEDIUM" if abs(_relative(current_value, previous_value) or 0) >= 0.20 else "LOW",
                    "evidence_ids": evidence_ids, "trend_engine_version": TREND_ENGINE_VERSION,
                    "current_days": current["dates"], "previous_days": previous["dates"],
                })
    findings.sort(key=lambda item: (-int(item["materiality"] == "MEDIUM"), item["trend_state"] == "STABLE", abs(item.get("relative_delta") or 0) * -1, item["trend_finding_id"]))
    has_missing = any(item["missing_buckets"] for item in coverage.values())
    max_days = max((item["days_available"] for item in coverage.values()), default=0)
    if not coverage or max_days < 2:
        history_health = "INSUFFICIENT_HISTORY"
    elif has_missing:
        history_health = "MISSING_BUCKETS"
    else:
        history_health = "HISTORY_OK" if max_days >= 14 else "HISTORY_SHORT"
    return {
        "trend_engine_version": TREND_ENGINE_VERSION,
        "history_days_available": max((item["days_available"] for item in coverage.values()), default=0),
        "completed_days": sorted({day for item in grouped.values() for day in [row["date"].isoformat() for row in item]}),
        "current_day_partial": True,
        "history_sufficient": bool(findings),
        "history_health": history_health,
        "coverage": coverage,
        "findings": findings[:max_findings],
        "counts": {state: sum(item["trend_state"] == state for item in findings) for state in ("IMPROVING", "STABLE", "DEGRADING", "VOLATILE", "RECOVERED", "NEW_SIGNAL")},
        "limits": {"total": max_findings, "negative": 5, "positive": 5, "volatile": 3},
        "policy": "deterministic daily aggregates only; current partial day is not a confirmed trend",
    }
