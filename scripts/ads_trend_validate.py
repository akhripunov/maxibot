#!/usr/bin/env python3
"""Read-only production validation of deterministic Trend Intelligence."""
from __future__ import annotations

import asyncio
import json
from datetime import date

from sqlalchemy import select

from app.ads.trends import build_trend_features
from app.db.models import AdsV2DailyAggregate
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        aggregates = list((await db.scalars(select(AdsV2DailyAggregate).order_by(AdsV2DailyAggregate.aggregate_date))).all())
    rows = [{
        "window": "1d", "date_from": row.aggregate_date, "date_to": row.aggregate_date,
        "dimension_type": row.dimension_type, "entity_id": row.entity_id,
        "dimension_value": row.dimension_value, "impressions": float(row.impressions or 0),
        "clicks": float(row.clicks or 0), "cost": float(row.cost or 0),
        "conversions": float(row.conversions or 0), "evidence_ids": [str(row.id)],
    } for row in aggregates]
    result = build_trend_features(rows, current_day=date.today())
    dimensions = ("CAMPAIGN", "AD_GROUP", "AD", "PLACEMENT", "REGION", "DEVICE", "DEMOGRAPHICS", "CRITERIA")
    by_dimension = {dimension: max((item["days_available"] for item in result["coverage"].values() if item["dimension"] == dimension), default=0) for dimension in dimensions}
    findings = [item for item in result["findings"] if item["dimension"] in {"CAMPAIGN", "PLACEMENT", "DEVICE", "DEMOGRAPHICS"} or item.get("entity") == "yandex.ru"]
    print(json.dumps({"history_health": result["history_health"], "history_days_available": result["history_days_available"], "completed_days": result["completed_days"], "coverage_days_by_dimension": by_dimension, "findings": findings, "counts": result["counts"]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
