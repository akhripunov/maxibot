"""Moscow-time daily channel reports sent by the single production worker."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.db.models import Admin, Channel, DailyChannelReport, TrafficEvent


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
                        subscribed: int, unsubscribed: int, invite_link_unsubscribed: int) -> str:
    count = str(subscriber_count) if subscriber_count is not None else "—"
    delta = f"{subscriber_delta:+d}" if subscriber_delta is not None else "—"
    undetermined = max(0, unsubscribed - invite_link_unsubscribed)
    return (
        f"⚡️ Дэйли: {title}\n"
        f"Подписчиков: {count} ({delta})\n\n"
        f"✅ Подписалось: +{subscribed}\n\n"
        f"⛔️ Отписалось: -{unsubscribed}\n"
        f"Пригласительные ссылки: -{invite_link_unsubscribed}\n"
        f"Не определено: -{undetermined}"
    )


async def _day_counts(db, channel_id: str, report_day: date) -> tuple[int, int]:
    start = datetime.combine(report_day, time.min, tzinfo=MOSCOW).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    subscribed = int(await db.scalar(select(func.count(TrafficEvent.id)).where(
        TrafficEvent.channel_id == channel_id,
        TrafficEvent.event_type == "user_added",
        TrafficEvent.event_time >= start,
        TrafficEvent.event_time < end,
    )) or 0)
    unsubscribed = int(await db.scalar(select(func.count(TrafficEvent.id)).where(
        TrafficEvent.channel_id == channel_id,
        TrafficEvent.event_type.in_(REMOVAL_EVENTS),
        TrafficEvent.event_time >= start,
        TrafficEvent.event_time < end,
    )) or 0)
    return subscribed, unsubscribed


async def send_due_daily_reports(client, session_factory, now: datetime | None = None) -> int:
    """Send at most one report per channel/day; failed deliveries are retried."""
    report_day = report_date_due(now or datetime.now(timezone.utc))
    if report_day is None:
        return 0
    delivered = 0
    async with session_factory() as db:
        rows = (await db.execute(select(Channel, Admin).join(Admin).order_by(Channel.title))).all()
    for channel, admin in rows:
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
            subscribed, unsubscribed = await _day_counts(db, channel.id, report_day)
            previous_count = await db.scalar(select(DailyChannelReport.subscriber_count).where(
                DailyChannelReport.channel_id == channel.id,
                DailyChannelReport.report_date < report_day,
                DailyChannelReport.status == "SENT",
                DailyChannelReport.subscriber_count.is_not(None),
            ).order_by(DailyChannelReport.report_date.desc()).limit(1))
            report.subscribed_count = subscribed
            report.unsubscribed_count = unsubscribed
            # MAX official membership updates do not disclose an invite source
            # for an unsubscribe. Keep this fail-closed rather than guessing.
            report.invite_link_unsubscribed_count = 0
            report.undetermined_unsubscribed_count = unsubscribed
            report.status = "SENDING"
            await db.commit()
        try:
            chat = await client.get_chat(channel.external_id)
            subscriber_count = chat_subscriber_count(chat)
            delta = subscriber_count - previous_count if subscriber_count is not None and previous_count is not None else None
            message = render_daily_report(channel.title, subscriber_count, delta, subscribed, unsubscribed, 0)
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
