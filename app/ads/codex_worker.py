from __future__ import annotations
import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.db.models import AdsAiRun

WAITING="WAITING_FOR_CODEX"; PROCESSING="CODEX_PROCESSING"; COMPLETED="CODEX_COMPLETED"; FAILED="CODEX_FAILED"
TERMINAL={COMPLETED, FAILED}

def transition(run: AdsAiRun, target: str) -> None:
    """Apply the small, explicit worker state machine."""
    allowed = {WAITING: {PROCESSING}, PROCESSING: TERMINAL}
    if target not in allowed.get(run.status, set()):
        raise ValueError(f"invalid worker transition: {run.status}->{target}")
    run.status = target

def worker_enabled() -> bool: return bool(settings.ads_enabled and settings.ads_codex_worker_enabled and settings.ads_llm_provider == "codex_worker")

async def claim_next_codex_run(db: AsyncSession, worker_id: str, worker_release_id: str | None = None) -> AdsAiRun | None:
    if not worker_enabled(): raise PermissionError("codex worker disabled")
    now=datetime.now(timezone.utc)
    async with db.begin():
        expired=list((await db.scalars(select(AdsAiRun).where(AdsAiRun.status==PROCESSING,AdsAiRun.lease_expires_at<now).with_for_update(skip_locked=True))).all())
        for run in expired:
            if run.retry_count>=settings.ads_codex_worker_max_retries: run.status=FAILED;run.validation_error="LEASE_RETRIES_EXHAUSTED"
            else: run.status=WAITING
        run=await db.scalar(select(AdsAiRun).where(AdsAiRun.status==WAITING).order_by(AdsAiRun.created_at).with_for_update(skip_locked=True))
        if not run:return None
        run.status=PROCESSING;run.worker_id=worker_id;run.worker_release_id=worker_release_id;run.claimed_at=now;run.lease_expires_at=now+timedelta(seconds=settings.ads_codex_worker_lease_seconds);run.lease_id=secrets.token_urlsafe(24);run.retry_count+=1
        return run

def lease_active(run: AdsAiRun, lease_id: str) -> bool:
    return run.status==PROCESSING and bool(run.lease_id) and secrets.compare_digest(run.lease_id,lease_id) and bool(run.lease_expires_at and run.lease_expires_at>datetime.now(timezone.utc))

async def fail_claimed_run(db: AsyncSession, run: AdsAiRun, *, retryable: bool, error_code: str, diagnostics: dict | None = None) -> None:
    if diagnostics is not None:
        import json
        run.worker_diagnostics_json = json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":"))[:12000]
    if retryable and run.retry_count < settings.ads_codex_worker_max_retries:
        run.status=WAITING; run.validation_error=error_code; run.worker_id=None; run.lease_id=None; run.lease_expires_at=None
    else: run.status=FAILED; run.validation_error=error_code
    await db.commit()
