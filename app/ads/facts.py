from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "UNKNOWN") else None
    except (TypeError, ValueError):
        return None


def _sum(rows: list[dict[str, Any]], field: str, days: set[str]) -> float | None:
    values = [_number(row.get(field)) for row in rows if str(row.get("Date")) in days]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _days(end: date, count: int) -> set[str]:
    from datetime import timedelta
    return {(end - timedelta(days=offset)).isoformat() for offset in range(count)}


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    return None if numerator is None or denominator in (None, 0) else numerator / denominator


@dataclass(frozen=True)
class CampaignFactSet:
    """Deterministic facts. ``None`` is the only representation of UNKNOWN."""
    campaign_age_days: int | None
    impressions_1d: float | None; impressions_3d: float | None; impressions_7d: float | None
    clicks_1d: float | None; clicks_3d: float | None; clicks_7d: float | None
    cost_1d: float | None; cost_3d: float | None; cost_7d: float | None
    conversions_1d: float | None; conversions_3d: float | None; conversions_7d: float | None
    ctr_7d: float | None; cpc_7d: float | None; conversion_rate_7d: float | None; actual_cpa_7d: float | None
    impressions_change_3d_vs_previous_3d: float | None; clicks_change_3d_vs_previous_3d: float | None
    cost_change_3d_vs_previous_3d: float | None; conversions_change_3d_vs_previous_3d: float | None
    landing_visits: int | None; max_redirects: int | None; subscriptions: int | None; qualified_subscriptions: int | None
    maxibot_qualified_membership_rate: float | None; data_freshness_hours: float | None; conversion_sample: float | None

    def evidence(self, *, report_date_from: date | None = None, report_date_to: date | None = None, report_fetched_at: datetime | None = None, snapshot_at: datetime | None = None) -> dict[str, Any]:
        values = asdict(self)
        evidence = {f"FACT-{index:03d}": {"name": key, "value": value} for index, (key, value) in enumerate(values.items(), 1)}
        metric_names = {"impressions", "clicks", "cost", "conversions"}
        for fact_id, item in evidence.items():
            name = item["name"]
            if "_1d" in name or "_3d" in name or "_7d" in name:
                metric, suffix = name.rsplit("_", 1)
                if metric in metric_names and report_date_to:
                    days = int(suffix[:-1])
                    from datetime import timedelta
                    date_from = report_date_to - timedelta(days=days - 1)
                    item["provenance"] = {
                        "fact_id": fact_id,
                        "metric": metric,
                        "window": suffix,
                        "source": "Yandex Direct Reports API / CAMPAIGN_PERFORMANCE_REPORT",
                        "source_field": metric.title(),
                        "raw_value": item["value"],
                        "date_from": date_from.isoformat(),
                        "date_to": report_date_to.isoformat(),
                        "timezone": "Europe/Moscow",
                        "aggregation": "sum rows by Date in inclusive calendar window",
                        "report_fetched_at": report_fetched_at.isoformat() if report_fetched_at else None,
                        "snapshot_at": snapshot_at.isoformat() if snapshot_at else None,
                        "freshness_caveat": "current calendar day may be incomplete or delayed" if suffix == "1d" else None,
                    }
        return evidence

    def context(self, *, report_date_from: date | None = None, report_date_to: date | None = None, report_fetched_at: datetime | None = None, snapshot_at: datetime | None = None) -> dict[str, Any]:
        return {"facts": asdict(self), "evidence": self.evidence(report_date_from=report_date_from, report_date_to=report_date_to, report_fetched_at=report_fetched_at, snapshot_at=snapshot_at)}


def build_campaign_facts(rows: list[dict[str, Any]], *, campaign_start: str | None, traffic: dict[str, int | float], now: datetime | None = None) -> CampaignFactSet:
    now = now or datetime.now(timezone.utc)
    end = now.astimezone(ZoneInfo("Europe/Moscow")).date()
    metrics: dict[str, float | None] = {}
    for field, prefix in (("Impressions", "impressions"), ("Clicks", "clicks"), ("Cost", "cost"), ("Conversions", "conversions")):
        for days in (1, 3, 7): metrics[f"{prefix}_{days}d"] = _sum(rows, field, _days(end, days))
    previous = {field: _sum(rows, source, {(end.fromordinal(end.toordinal() - offset)).isoformat() for offset in range(3, 6)}) for field, source in (("impressions", "Impressions"), ("clicks", "Clicks"), ("cost", "Cost"), ("conversions", "Conversions"))}
    def change(current: float | None, old: float | None) -> float | None: return None if current is None or old in (None, 0) else (current - old) / old
    age = None
    try: age = max(0, (end - date.fromisoformat(str(campaign_start))).days) if campaign_start else None
    except ValueError: pass
    visits, exact, high = traffic.get("visits"), traffic.get("EXACT"), traffic.get("HIGH")
    known_qualified = (int(exact) + int(high)) if exact is not None and high is not None else None
    return CampaignFactSet(
        campaign_age_days=age, **metrics,
        ctr_7d=_ratio(metrics["clicks_7d"], metrics["impressions_7d"]), cpc_7d=_ratio(metrics["cost_7d"], metrics["clicks_7d"]),
        conversion_rate_7d=_ratio(metrics["conversions_7d"], metrics["clicks_7d"]), actual_cpa_7d=_ratio(metrics["cost_7d"], metrics["conversions_7d"]),
        impressions_change_3d_vs_previous_3d=change(metrics["impressions_3d"], previous["impressions"]), clicks_change_3d_vs_previous_3d=change(metrics["clicks_3d"], previous["clicks"]), cost_change_3d_vs_previous_3d=change(metrics["cost_3d"], previous["cost"]), conversions_change_3d_vs_previous_3d=change(metrics["conversions_3d"], previous["conversions"]),
        landing_visits=int(visits) if visits is not None else None, max_redirects=int(traffic["to_channel"]) if traffic.get("to_channel") is not None else None, subscriptions=int(traffic["new_members"]) if traffic.get("new_members") is not None else None, qualified_subscriptions=known_qualified,
        maxibot_qualified_membership_rate=_ratio(float(known_qualified) if known_qualified is not None else None, float(visits) if visits is not None else None), data_freshness_hours=0.0 if rows else None, conversion_sample=metrics["conversions_7d"],
    )
