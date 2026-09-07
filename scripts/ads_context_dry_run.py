"""Read-only AI context size audit for one persisted AdsCampaign."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from app.ads.analysis import build_v2_analysis_dataset, campaign_fact_set_from_v2
from app.ads.attribution import build_maxibot_metrics
from app.ads.context_budget import bound_ai_context
from app.ads.health import data_health, sufficiency
from app.ads.llm import provider_from_settings
from app.ads.memory import retrieve
from app.ads.service import _context
from app.core.config import settings
from app.db.models import AdsCampaign, AdsPromotionCampaignLink
from app.db.session import SessionLocal
from app.traffic.analytics import traffic_stats_for_channels


def size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


async def main(external_id: str) -> None:
    async with SessionLocal() as db:
        campaign = await db.scalar(select(AdsCampaign).where(AdsCampaign.external_id == external_id))
        if not campaign:
            raise SystemExit("campaign not found")
        now = datetime.now(timezone.utc)
        today = now.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow")).date()
        dataset = await build_v2_analysis_dataset(db, campaign=campaign)
        channel_id = await db.scalar(select(AdsPromotionCampaignLink.channel_id).where(
            AdsPromotionCampaignLink.object_type == "CHANNEL",
            AdsPromotionCampaignLink.campaign_id == campaign.id,
            AdsPromotionCampaignLink.status == "ACTIVE",
        ))
        if not channel_id:
            raise SystemExit("campaign has no active channel mapping")
        traffic = await traffic_stats_for_channels(db, [channel_id])
        metrics = await build_maxibot_metrics(db, channel_id=channel_id, end_date=today)
        fact_set = campaign_fact_set_from_v2(dataset, traffic=traffic)
        fact_context = fact_set.context(
            report_date_from=today - timedelta(days=6), report_date_to=today,
            report_fetched_at=now, snapshot_at=now,
        )
        suff, missing = sufficiency(
            mapped_campaigns=1,
            report_days=int(dataset["data_quality"].get("history_days", 0)),
            conversions=fact_set.conversion_sample,
            attribution_available=bool(traffic.get("visits")),
            direct_available=bool(dataset["data_quality"].get("rows_loaded")),
        )
        health = data_health(
            direct_available=bool(dataset["data_quality"].get("rows_loaded")),
            mapped_campaigns=1, attribution_available=bool(traffic.get("visits")),
            snapshot_fresh=True, ai_available=bool(provider_from_settings()),
            memory_db_available=True, unmatched_attribution=int(traffic.get("UNATTRIBUTED", 0)),
        )
        facts = {
            "campaign": {"id": campaign.id, "name": campaign.name},
            **fact_context, "data_sufficiency": suff, "missing_data": missing,
            "data_health": health, "MAXIBOT_METRICS": metrics,
            "TRENDS": dataset.get("trends", {}),
            "V2_ANALYSIS": {key: value for key, value in dataset.items() if key != "trends"},
        }
        memories = await retrieve(db, channel_id=channel_id, campaign_id=campaign.id)
        context = _context(campaign, facts, suff, missing, memories)
        context, final_budget = bound_ai_context(
            context,
            target_bytes=min(60000, settings.ads_ai_context_max_bytes - 1000),
            max_bytes=settings.ads_ai_context_max_bytes,
            max_evidence=settings.ads_ai_evidence_max_entries,
        )
        print(json.dumps({
            "campaign": campaign.name,
            "context_bytes": size(context),
            "final_budget": final_budget,
            "dataset_bytes": size(dataset),
            "section_bytes": {key: size(value) for key, value in {
                "mandatory_campaign": context.get("CURRENT_CAMPAIGN"),
                "facts": facts,
                "granular": facts["V2_ANALYSIS"].get("sections"),
                "trends": facts["TRENDS"],
                "maxibot_attribution": facts.get("MAXIBOT_METRICS"),
                "data_health": facts.get("data_health"),
                "evidence": facts["V2_ANALYSIS"].get("evidence"),
                "static_context": {key: context[key] for key in (
                    "DATA_SUFFICIENCY", "MISSING_DATA", "ALLOWED_RECOMMENDATION_TYPES",
                    "SYSTEM_CONSTRAINTS",
                )},
            }.items()},
            "rows_loaded": dataset["data_quality"].get("rows_loaded"),
            "daily_history_rows": dataset["provenance_summary"].get("observation_rows_analyzed"),
            "history_days": dataset["trends"].get("history_days_available"),
            "trend_findings": len(dataset["trends"].get("findings", [])),
            "trend_summary": [
                {key: finding.get(key) for key in (
                    "dimension", "entity", "metric", "period", "trend_state",
                    "current_value", "previous_value", "relative_delta",
                )}
                for finding in dataset["trends"].get("findings", [])
            ],
            "evidence_count": len(dataset["evidence"]),
            "raw_daily_dates_in_ai_trends": "completed_days" in dataset["trends"],
            "target_bytes": 60000,
            "hard_limit": settings.ads_ai_context_max_bytes,
        }, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("external_id")
    args = parser.parse_args()
    asyncio.run(main(args.external_id))
