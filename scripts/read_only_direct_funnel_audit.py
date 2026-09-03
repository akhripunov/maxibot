from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Channel, MaxJoinEvent, TrafficEvent, TrafficMembership, TrafficVisit
from app.db.session import SessionLocal

CAMPAIGN = "m365_rsya_msk_conv_test2"
STAGES = (
    "LANDING", "STARTAPP", "MAX_LINK_ASSIGNED", "PAGE_HIDDEN",
    "HANDOFF_CONFIRMED", "MINIAPP_LOADED", "RESOLVE_OK", "IDENTITY",
    "CHANNEL_OPEN",
)
EVENT_STAGE = {
    "LANDING_RENDERED": "LANDING", "JS_LANDING_STARTED": "LANDING",
    "STARTAPP_READY": "STARTAPP", "MAX_LINK_ASSIGNED": "MAX_LINK_ASSIGNED",
    "PAGE_HIDDEN": "PAGE_HIDDEN", "HANDOFF_CONFIRMED": "HANDOFF_CONFIRMED",
    "MINI_APP_LOADED": "MINIAPP_LOADED", "RESOLVE_OK": "RESOLVE_OK",
    "max_identity_linked": "IDENTITY", "MINIAPP_CHANNEL_HANDOFF_STARTED": "CHANNEL_OPEN",
    "channel_open_clicked": "CHANNEL_OPEN",
}
RANK = {name: i for i, name in enumerate(STAGES)}

def device(value: str | None) -> str:
    text = (value or "").lower()
    if "android" in text: return "Android"
    if "iphone" in text or "ipad" in text or "ios" in text: return "iPhone/iPad"
    if "macintosh" in text or "mac os" in text: return "Mac"
    if "windows" in text: return "Windows"
    if "linux" in text: return "Linux"
    return "Other/unknown"

def campaign_match(value: str | None) -> bool:
    value = (value or "").strip()
    return value == CAMPAIGN or value.startswith(CAMPAIGN + ".")

def source(v: TrafficVisit) -> str:
    src = (v.utm_source or "").lower()
    ref = (v.referer or "").lower()
    if "yandex" in src or src == "direct" or "yandex" in ref or v.yclid:
        return "Yandex Direct"
    return src or "other/unknown"

async def main() -> None:
    async with SessionLocal() as db:
        channel = await db.scalar(select(Channel).where(Channel.title == "Москва 365"))
        if not channel:
            raise SystemExit("channel not found")
        visits = list((await db.scalars(select(TrafficVisit).where(TrafficVisit.channel_id == channel.id).order_by(TrafficVisit.landing_at))).all())
        events = list((await db.scalars(select(TrafficEvent).where(TrafficEvent.channel_id == channel.id).order_by(TrafficEvent.event_time))).all())
        by_visit: dict[str, list[TrafficEvent]] = defaultdict(list)
        for event in events:
            if event.visit_id:
                by_visit[event.visit_id].append(event)
        rows = []
        for v in visits:
            matched = campaign_match(v.utm_campaign)
            direct = bool(v.yclid and matched)
            if not direct:
                continue
            stages = set()
            devices = set()
            for e in by_visit.get(v.id, []):
                if e.event_type in EVENT_STAGE:
                    stages.add(EVENT_STAGE[e.event_type])
                try:
                    meta = json.loads(e.metadata_json or "{}")
                    devices.add(device(meta.get("device_family") or meta.get("user_agent")))
                except Exception:
                    pass
            if v.startapp_token: stages.add("STARTAPP")
            if v.max_user_id: stages.add("IDENTITY")
            # `to_channel_at` is assigned when the traffic resolver issues the
            # target, not when the MAX client actually opens it.  Count the
            # runtime channel-open signals only.
            if "CHANNEL_OPEN" in stages:
                stages.add("CHANNEL_OPEN")
            if not stages: stages.add("LANDING")
            rows.append({"yclid": v.yclid, "device": sorted(devices)[0] if devices else device(v.user_agent), "source": source(v), "stages": stages, "landing_at": v.landing_at.isoformat()})
        unique: dict[str, dict] = {}
        for r in rows:
            item = unique.setdefault(r["yclid"], {"devices": set(), "source": r["source"], "stages": set(), "landing_at": r["landing_at"]})
            item["devices"].add(r["device"]); item["stages"].update(r["stages"])
        def bucket(item):
            return max(item["stages"], key=lambda s: RANK[s])
        stage_counts = Counter(bucket(x) for x in unique.values())
        device_raw = Counter(r["device"] for r in rows)
        device_unique = Counter()
        device_stage = defaultdict(Counter)
        for item in unique.values():
            d = sorted(item["devices"])[0] if item["devices"] else "Other/unknown"
            device_unique[d] += 1; device_stage[d][bucket(item)] += 1
        matched_visits = [v for v in visits if campaign_match(v.utm_campaign)]
        yclid_visits = [v for v in matched_visits if v.yclid]
        campaign_ids = {v.id for v in matched_visits if v.yclid}
        direct_event_counts = Counter(e.event_type for e in events if e.visit_id in campaign_ids)
        memberships = list((await db.scalars(select(TrafficMembership).where(TrafficMembership.channel_id == channel.id))).all())
        direct_memberships = [m for m in memberships if m.attributed_visit_id in campaign_ids]
        direct_member_users = {m.max_user_id for m in direct_memberships}
        join_events = list((await db.scalars(select(MaxJoinEvent).where(MaxJoinEvent.channel_id == channel.id))).all())
        direct_user_added = sum(1 for e in join_events if e.event_type.lower() == "user_added" and e.max_user_id in direct_member_users)
        all_stage_counts = {s: sum(1 for x in unique.values() if s in x["stages"]) for s in STAGES}
        print(json.dumps({
            "scope": {"channel": "Москва 365", "campaign": CAMPAIGN, "available_from": visits[0].landing_at.isoformat() if visits else None, "available_to": visits[-1].landing_at.isoformat() if visits else None},
            "direct": {"raw_requests": len(rows), "unique_yclid": len(unique), "repeat_or_revisit": len(rows)-len(unique), "missing_yclid_campaign_requests": len(matched_visits)-len(yclid_visits), "stage_counts_unique_yclid": all_stage_counts, "deepest_stage_unique_yclid": dict(stage_counts), "source": dict(Counter(r["source"] for r in rows))},
            "devices": {d: {"raw_requests": device_raw[d], "unique_yclid": device_unique[d], "deepest_stage": dict(device_stage[d])} for d in sorted(set(device_raw)|set(device_unique))},
            "memberships": {"approved_user_added_on_direct_visit": direct_user_added, "exact": sum(1 for m in direct_memberships if m.attribution_status == "EXACT"), "unattributed": sum(1 for m in direct_memberships if m.attribution_status != "EXACT"), "direct_membership_rows": len(direct_memberships)},
            "event_counts_direct": dict(direct_event_counts),
            "visit_status_direct": dict(Counter(v.status for v in yclid_visits)),
            "visit_to_channel_at_present": sum(1 for v in yclid_visits if v.to_channel_at is not None),
        }, ensure_ascii=False))

asyncio.run(main())
