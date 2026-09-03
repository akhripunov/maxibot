from __future__ import annotations
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AdsMemory, AdsMemoryEvidence

ACTIVE = {"VALIDATED", "TRUSTED"}

async def retrieve(db: AsyncSession, *, channel_id: str, campaign_id: str | None) -> list[AdsMemory]:
    query = select(AdsMemory).where(AdsMemory.channel_id == channel_id, AdsMemory.scope == "PRIVATE", AdsMemory.status.in_(ACTIVE))
    if campaign_id: query = query.order_by((AdsMemory.campaign_id == campaign_id).desc(), AdsMemory.updated_at.desc())
    return list((await db.scalars(query.limit(12))).all())

async def create_candidates(db: AsyncSession, *, channel_id: str, campaign_id: str | None, ai_run_id: str, snapshot_id: str, candidates: list[dict], known_evidence: set[str]) -> list[AdsMemory]:
    created: list[AdsMemory] = []
    for item in candidates:
        statement = str(item.get("statement") or "").strip()
        refs = item.get("source_evidence_ids") or []
        if not statement or not refs or any(ref not in known_evidence for ref in refs): continue
        duplicate = await db.scalar(select(AdsMemory).where(AdsMemory.channel_id == channel_id, AdsMemory.content == statement, AdsMemory.status != "REJECTED"))
        if duplicate: continue
        memory = AdsMemory(channel_id=channel_id, campaign_id=campaign_id, content=statement, evidence_ids_json=json.dumps(refs), source_type="AI_INFERENCE", status="CANDIDATE")
        db.add(memory); await db.flush()
        for ref in refs: db.add(AdsMemoryEvidence(memory_id=memory.id, evidence_type="FACT", evidence_ref_id=ref, snapshot_id=snapshot_id, ai_run_id=ai_run_id))
        created.append(memory)
    return created

async def set_status(db: AsyncSession, *, memory_id: str, channel_id: str, status: str) -> bool:
    memory = await db.scalar(select(AdsMemory).where(AdsMemory.id == memory_id, AdsMemory.channel_id == channel_id))
    if not memory or status not in {"VALIDATED", "TRUSTED", "REJECTED"}: return False
    memory.status = status; await db.commit(); return True
