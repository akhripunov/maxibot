"""Moscow-time daily channel reports sent by the single production worker."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.db.models import Admin, Channel, DailyChannelReport, TrafficEvent, TrafficMembership


logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")
DAILY_SEND_TIME = time(hour=8, minute=30)
REMOVAL_EVENTS = ("user_removed", "member_removed", "unsubscribed")


def report_date_due(now: datetime) -> date | None:
    """Return the prior Moscow day when its 08:30 report is due."""
    local = now.astimezone(MOSCOW)
    if local.timetz().replace(tzinfo=None) < DAILY_SEND_TIME:
        return None
    return local.date() - timedelta(days=1)


def channel_existed_on_report_day(connected_at: datetime | None, report_day: date) -> bool:
    """Do not send a historical daily report immediately after connection."""
    if connected_at is None:
        return True
    if connected_at.tzinfo is None:
        connected_at = connected_at.replace(tzinfo=timezone.utc)
    return connected_at.astimezone(MOSCOW).date() <= report_day


def chat_subscriber_count(payload: dict | None) -> int | None:
    """Read only a documented/observed aggregate count, never infer one."""
    if not isinstance(payload, dict):
        return None
    candidates = (payload, payload.get("chat"), payload.get("result"))
    for item in candidates:
        if not isinstance(item, dict):
            continue
        for key in ("participants_count", "members_count", "subscribers_count"):
            value = item.get(key)
            if isinstance(value, int) and value >= 0:
                return value
    return None


def render_daily_report(title: str, subscriber_count: int | None, subscriber_delta: int | None,
                        subscribed: int, unsubscribed: int, invite_link_unsubscribed: int,
                        confirmed_paid: int = 0, source_unconfirmed: int | None = None,
                        confirmed_unsubscribed: int = 0) -> str:
    count = str(subscriber_count) if subscriber_count is not None else "—"
    delta = f"{subscriber_delta:+d}" if subscriber_delta is not None else "—"
    undetermined = max(0, unsubscribed - invite_link_unsubscribed)
    undetermined_unsubscribed = max(0, undetermined - confirmed_unsubscribed)
    source_unconfirmed = subscribed - confirmed_paid if source_unconfirmed is None else source_unconfirmed
    return (
        f"⚡️ Дэйли: {title}\n"
        f"Подписчиков сейчас: {count}\n"
        f"Изменение за прошлый день: {delta}\n\n"
        f"✅ Подписалось: +{subscribed}\n"
        f"   ├─ подтверждено из рекламы: +{confirmed_paid}\n"
        f"   └─ источник не подтверждён: +{source_unconfirmed}\n\n"
        f"⛔️ Отписалось: -{unsubscribed}\n"
        f"   ├─ подтверждено из рекламы: -{confirmed_unsubscribed}\n"
        f"   └─ источник не подтверждён: -{undetermined_unsubscribed}\n"
        f"Пригласительные ссылки: -{invite_link_unsubscribed}\n"
        f"Не определено: -{undetermined_unsubscribed}"
    )


async def _day_counts(db, channel_id: str, report_day: date) -> tuple[int, int, int, int, int]:
    start = datetime.combine(report_day, time.min, tzinfo=MOSCOW).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    subscribed = int(await db.scalar(select(func.count(TrafficEvent.id)).where(
        TrafficEvent.channel_id == channel_id,
        TrafficEvent.event_type == "user_added",
        TrafficEvent.event_time >= start,
        TrafficEvent.event_time < end,
    )) or 0)
    exact = int(await db.scalar(select(func.count(TrafficMembership.id)).join(
        TrafficEvent, TrafficMembership.source_event_id == TrafficEvent.id,
    ).where(
        TrafficMembership.channel_id == channel_id,
        TrafficMembership.attribution_status == "EXACT",
        TrafficEvent.event_type == "user_added",
        TrafficEvent.event_time >= start,
        TrafficEvent.event_time < end,
    )) or 0)
    # All non-EXACT memberships remain explicitly unconfirmed.  They must not
    # be presented as organic because MAX does not prove an organic source.
    source_unconfirmed = max(0, subscribed - exact)
    unsubscribed = int(await db.scalar(select(func.count(TrafficEvent.id)).where(
        TrafficEvent.channel_id == channel_id,
        TrafficEvent.event_type.in_(REMOVAL_EVENTS),
        TrafficEvent.event_time >= start,
        TrafficEvent.event_time < end,
    )) or 0)
    confirmed_unsubscribed = int(await db.scalar(select(func.count(TrafficEvent.id)).join(
        TrafficMembership,
        (TrafficMembership.channel_id == TrafficEvent.channel_id)
        & (TrafficMembership.max_user_id == TrafficEvent.max_user_id),
    ).where(
        TrafficEvent.channel_id == channel_id,
        TrafficEvent.event_type.in_(REMOVAL_EVENTS),
        TrafficEvent.event_time >= start,
        TrafficEvent.event_time < end,
        TrafficMembership.attribution_status == "EXACT",
    )) or 0)
    return subscribed, exact, source_unconfirmed, unsubscribed, confirmed_unsubscribed


async def send_due_daily_reports(client, session_factory, now: datetime | None = None) -> int:
    """Send at most one report per channel/day; failed deliveries are retried."""
    report_day = report_date_due(now or datetime.now(timezone.utc))
    if report_day is None:
        return 0
    delivered = 0
    async with session_factory() as db:
        rows = (await db.execute(select(Channel, Admin).join(Admin).order_by(Channel.title))).all()
    for channel, admin in rows:
        # A channel connected today must first appear in tomorrow's report;
        # never backfill yesterday's report at the moment of connection.
        if not channel_existed_on_report_day(channel.connected_at, report_day):
            continue
        async with session_factory() as db:
            report = await db.scalar(select(DailyChannelReport).where(
                DailyChannelReport.channel_id == channel.id,
                DailyChannelReport.report_date == report_day,
            ))
            if report and report.status == "SENT":
                continue
            if not report:
                report = DailyChannelReport(channel_id=channel.id, report_date=report_day)
                db.add(report)
            subscribed, confirmed_paid, source_unconfirmed, unsubscribed, confirmed_unsubscribed = await _day_counts(db, channel.id, report_day)
            report.subscribed_count = subscribed
            report.unsubscribed_count = unsubscribed
            report.invite_link_unsubscribed_count = 0
            report.undetermined_unsubscribed_count = max(0, unsubscribed - confirmed_unsubscribed)
            report.status = "SENDING"
            await db.commit()
        try:
            chat = await client.get_chat(channel.external_id)
            subscriber_count = chat_subscriber_count(chat)
            # The daily change must describe the report day itself, not the
            # difference between two live snapshots taken at different times.
            # This keeps the headline consistent with the event totals below
            # and includes all official MAX membership events, regardless of
            # advertising source.
            delta = subscribed - unsubscribed
            message = render_daily_report(channel.title, subscriber_count, delta, subscribed, unsubscribed, 0,
                                          confirmed_paid, source_unconfirmed, confirmed_unsubscribed)
            await client.send_message(admin.external_id, message, disable_link_preview=True)
        except Exception as exc:
            logger.exception("DAILY_REPORT_SEND_FAILED channel_id=%s error=%s", channel.id, type(exc).__name__)
            async with session_factory() as db:
                report = await db.scalar(select(DailyChannelReport).where(
                    DailyChannelReport.channel_id == channel.id,
                    DailyChannelReport.report_date == report_day,
                ))
                if report:
                    report.status = "FAILED"
                    await db.commit()
            continue
        async with session_factory() as db:
            report = await db.scalar(select(DailyChannelReport).where(
                DailyChannelReport.channel_id == channel.id,
                DailyChannelReport.report_date == report_day,
            ))
            if report:
                report.subscriber_count = subscriber_count
                report.subscriber_delta = delta
                report.status = "SENT"
                report.sent_at = datetime.now(timezone.utc)
                await db.commit()
        delivered += 1
    return delivered
