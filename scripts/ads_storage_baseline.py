#!/usr/bin/env python3
"""Capture a safe, non-destructive ADS storage baseline.

The default output is stdout so an operator can retain snapshots externally.
With ``--output`` the script appends JSONL records to an explicitly selected
file; it never deletes, prunes, or changes database data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.ads.storage_health import diagnostics
from app.db.models import AdsV2CollectionRun, AdsV2Entity, AdsV2Observation, AdsV2ReportRun
from app.db.session import SessionLocal


async def snapshot() -> dict[str, object]:
    async with SessionLocal() as db:
        health = await diagnostics(db)
        counts = {
            "observations": int(await db.scalar(select(func.count(AdsV2Observation.id))) or 0),
            "report_runs": int(await db.scalar(select(func.count(AdsV2ReportRun.id))) or 0),
            "collection_runs": int(await db.scalar(select(func.count(AdsV2CollectionRun.id))) or 0),
            "entities": int(await db.scalar(select(func.count(AdsV2Entity.id))) or 0),
        }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "ads_relation_bytes": health["ads_total"],
        "database_bytes": health["database"]["total"],
        "vps": health["disk"],
        "rows": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="append JSONL to this explicit path")
    args = parser.parse_args()
    record = json.dumps(asyncio.run(snapshot()), ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("a", encoding="utf-8") as handle:
            handle.write(record + "\n")
    print(record)


if __name__ == "__main__":
    main()
