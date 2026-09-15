#!/usr/bin/env python3
"""Operator-only compare-and-set execution-region tool.

This tool is intentionally not exposed through an API or UI.  B0 ships it in
dry-run mode; a future operator must explicitly pass --apply after remote
health, identity and owner assertions have been independently verified.
"""
from __future__ import annotations

import argparse
import asyncio
from sqlalchemy import select, update
from app.db.models import AdsAccount
from app.db.session import SessionLocal


async def main(args: argparse.Namespace) -> int:
    async with SessionLocal() as db:
        account = await db.scalar(select(AdsAccount).where(AdsAccount.id == args.account_id))
        if account is None or account.provider != "TELEGRAM_ADS":
            raise SystemExit("ACCOUNT_NOT_FOUND_OR_PROVIDER_MISMATCH")
        if account.storage_region != "RU":
            raise SystemExit("STORAGE_REGION_MUST_REMAIN_RU")
        if account.execution_region != args.expected_current:
            raise SystemExit("EXECUTION_REGION_COMPARE_AND_SET_FAILED")
        if args.target not in {"RU", "EU"}:
            raise SystemExit("INVALID_TARGET_REGION")
        print(f"DRY_RUN account={account.id} {account.execution_region}->{args.target} reason={args.reason}")
        if args.apply:
            await db.execute(update(AdsAccount).where(AdsAccount.id == args.account_id, AdsAccount.execution_region == args.expected_current).values(execution_region=args.target))
            await db.commit()
            print("APPLIED=YES")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("account_id")
    parser.add_argument("--expected-current", default="RU")
    parser.add_argument("--target", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args())))

