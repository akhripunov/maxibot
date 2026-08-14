"""Dedicated MAX Long Polling worker.

Keeping polling outside the HTTP process prevents a web-server lifecycle issue
from silently stopping bot updates.
"""

import asyncio
import contextlib
import logging

from app.bot.runtime import MaxBotRuntime
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.integrations.max_client import RealMaxClient

logger = logging.getLogger(__name__)


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    client = RealMaxClient()
    runtime = MaxBotRuntime(client, SessionLocal)
    try:
        logger.warning("MAX polling worker started")
        await runtime.run_forever()
    finally:
        await runtime.stop()
        await client.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
