from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    Admin,
    Base,
    Channel,
    LandingPage,
    TrafficEvent,
    TrafficMembership,
    TrafficVisit,
)
from app.traffic.analytics import traffic_stats_for_channels


@pytest.mark.asyncio
async def test_all_time_ad_subscriptions_match_daily_report_event_semantics():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            admin = Admin(external_id="analytics-owner")
            db.add(admin)
            await db.flush()
            channel = Channel(admin_id=admin.id, external_id="channel", title="Channel", public_slug="analytics-channel")
            db.add(channel)
            await db.flush()
            landing = LandingPage(channel_id=channel.id, public_id="analytics-landing", title="Landing", description="Test")
            db.add(landing)
            await db.flush()
            visit = TrafficVisit(
                visit_token="visit-1", landing_page_id=landing.id, channel_id=channel.id,
                yclid="yclid-1", landing_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            )
            db.add(visit)
            await db.flush()
            db.add_all([
                TrafficMembership(channel_id=channel.id, max_user_id="user-1", attribution_status="EXACT", attributed_visit_id=visit.id),
                TrafficMembership(channel_id=channel.id, max_user_id="user-2", attribution_status="UNATTRIBUTED", current_member=False),
            ])
            event_rows = [
                # Duplicate delivery for the same user/day counts once, as in a daily report.
                ("a1", "user-1", "user_added", datetime(2026, 9, 1, 8, tzinfo=timezone.utc)),
                ("a2", "user-1", "user_added", datetime(2026, 9, 1, 9, tzinfo=timezone.utc)),
                # A later rejoin on another day contributes another daily total.
                ("a3", "user-1", "user_added", datetime(2026, 9, 2, 8, tzinfo=timezone.utc)),
                ("a4", "user-2", "user_added", datetime(2026, 9, 2, 9, tzinfo=timezone.utc)),
                ("r1", "user-1", "user_removed", datetime(2026, 9, 3, 8, tzinfo=timezone.utc)),
                ("r2", "user-1", "member_removed", datetime(2026, 9, 3, 9, tzinfo=timezone.utc)),
                ("r3", "user-2", "unsubscribed", datetime(2026, 9, 4, 8, tzinfo=timezone.utc)),
            ]
            db.add_all([
                TrafficEvent(
                    channel_id=channel.id, max_user_id=user_id, event_type=event_type,
                    event_time=event_time, dedupe_key=key,
                )
                for key, user_id, event_type, event_time in event_rows
            ])
            await db.commit()

            stats = await traffic_stats_for_channels(db, [channel.id])
            assert stats["lifetime_ads_subscribed"] == 2
            assert stats["lifetime_ads_unsubscribed"] == 1
            # Existing unique-member/state metrics remain available to ADS logic.
            assert stats["new_members"] == 2
            assert stats["unsubscribed"] == 1
    finally:
        await engine.dispose()


def test_bot_and_miniapp_analytics_render_daily_style_lifetime_totals():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    menu = (root / "app/bot/menu.py").read_text()
    miniapp = (root / "miniapp/app.js").read_text()
    for source in (menu, miniapp):
        assert "lifetime_ads_subscribed" in source
        assert "lifetime_ads_unsubscribed" in source
        assert "Подписалось с рекламы за всё время" in source
        assert "Отписалось с рекламы за всё время" in source


def test_client_analytics_does_not_render_internal_attribution_diagnostics():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    menu = (root / "app/bot/menu.py").read_text()
    miniapp = (root / "miniapp/app.js").read_text()
    assert "MAX signals:" not in menu
    assert "MAX signals:" not in miniapp
    assert "В Яндекс отправляются только EXACT/HIGH" not in menu
    assert "В Яндекс отправляются только EXACT/HIGH" not in miniapp
