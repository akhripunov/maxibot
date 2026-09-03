import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import OfflineConversion, TrafficEvent, TrafficMembership, TrafficVisit
from app.db.session import SessionLocal


async def main() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(OfflineConversion, TrafficMembership, TrafficVisit)
            .join(TrafficMembership, TrafficMembership.id == OfflineConversion.membership_id)
            .join(TrafficVisit, TrafficVisit.id == OfflineConversion.visit_id)
            .where(OfflineConversion.created_at >= cutoff)
            .order_by(OfflineConversion.created_at.desc())
            .limit(5)
        )).all()
        result = []
        for conversion, membership, visit in rows:
            event_count = await db.scalar(select(func.count(TrafficEvent.id)).where(
                TrafficEvent.visit_id == visit.id,
                TrafficEvent.event_type == "METRICA_HIT_CONFIRMED",
            ))
            result.append({
                "queue_status": conversion.status,
                "attempts": conversion.attempts,
                "last_error": conversion.last_error,
                "attribution": membership.attribution_status,
                "yclid_present": bool(visit.yclid),
                "client_id_present": bool(visit.metrica_client_id),
                "metrica_hit_confirmed": bool(event_count),
                "landing_at": visit.landing_at.isoformat(),
                "created_at": conversion.created_at.isoformat(),
            })
        print({"count": len(result), "rows": result})


asyncio.run(main())
