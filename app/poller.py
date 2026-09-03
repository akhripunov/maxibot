"""Dedicated MAX Long Polling worker.

Keeping polling outside the HTTP process prevents a web-server lifecycle issue
from silently stopping bot updates.
"""

import asyncio
import contextlib
import logging
import os
import socket

from app.bot.runtime import MaxBotRuntime
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.integrations.max_client import RealMaxClient
from app.ads.outcome_collector import process_due_outcomes
from app.ads.periodic import process_periodic_collection
from app.traffic.membership_reconciliation import reconcile_active_memberships
from app.traffic.metrica_upload_tracker import refresh_metrica_upload_statuses

logger = logging.getLogger(__name__)


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    client = RealMaxClient()
    runtime = MaxBotRuntime(client, SessionLocal)
    async def outcome_loop():
        while True:
            try:
                processed = await process_due_outcomes(SessionLocal)
                if processed:
                    logger.info("ADS_V2_OUTCOMES_PROCESSED count=%s", processed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ADS_V2_OUTCOME_CYCLE_FAILED")
            await asyncio.sleep(60)
    async def periodic_collection_loop():
        # This loop is deliberately independent of MAX polling and Codex.
        # The setting defaults to false and is also pinned false in the VPS
        # compose file until the canary is explicitly enabled.
        while True:
            try:
                processed = await process_periodic_collection(SessionLocal)
                if processed:
                    logger.info("ADS_V2_PERIODIC_COLLECTION_PROCESSED count=%s", processed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ADS_V2_PERIODIC_COLLECTION_CYCLE_FAILED")
            await asyncio.sleep(60)
    async def membership_reconciliation_loop():
        """Recover verified members if a MAX update was unavailable."""
        while True:
            try:
                recovered = await reconcile_active_memberships(client, SessionLocal)
                if recovered:
                    logger.warning("MAX_MEMBERSHIP_RECONCILED count=%s", recovered)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MAX_MEMBERSHIP_RECONCILIATION_FAILED")
            await asyncio.sleep(60)
    async def metrica_upload_status_loop():
        while True:
            try:
                refreshed = await refresh_metrica_upload_statuses(SessionLocal)
                if refreshed:
                    logger.info("METRICA_UPLOAD_STATUSES_REFRESHED count=%s", refreshed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("METRICA_UPLOAD_STATUS_REFRESH_FAILED")
            await asyncio.sleep(60)
    outcome_task = asyncio.create_task(outcome_loop())
    periodic_task = asyncio.create_task(periodic_collection_loop())
    reconciliation_task = asyncio.create_task(membership_reconciliation_loop())
    metrica_status_task = asyncio.create_task(metrica_upload_status_loop())
    try:
        logger.warning("MAX polling worker started")
        logger.warning(
            "MAX_WORKER_INSTANCE instance=%s host=%s pid=%s release=%s",
            f"{socket.gethostname()}:{os.getpid()}",
            socket.gethostname(),
            os.getpid(),
            os.environ.get("MAXIBOT_RELEASE_MARKER", "unknown"),
        )
        await runtime.run_forever()
    finally:
        outcome_task.cancel()
        periodic_task.cancel()
        reconciliation_task.cancel()
        metrica_status_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await outcome_task
        with contextlib.suppress(asyncio.CancelledError):
            await periodic_task
        with contextlib.suppress(asyncio.CancelledError):
            await reconciliation_task
        with contextlib.suppress(asyncio.CancelledError):
            await metrica_status_task
        await runtime.stop()
        await client.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
