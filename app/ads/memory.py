from __future__ import annotations
import json
import hashlib
import re
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AdsMemory, AdsMemoryEvidence

ACTIVE = {"SUPPORTED", "VALIDATED", "TRUSTED"}
MEMORY_STATUSES = {"CANDIDATE", "INSUFFICIENT_EVIDENCE", "SUPPORTED", "VALIDATED", "TRUSTED", "REJECTED", "SUPERSEDED", "STALE"}


def claim_key(statement: str, *, objective: str | None = None, action: str | None = None) -> str:
    """Stable semantic key used for deterministic deduplication/contradictions."""
    normalized = re.sub(r"[^a-zа-я0-9]+", " ", str(statement or "").lower()).strip()
    prefix = "|".join(str(value or "").lower() for value in (objective, action))
    return hashlib.sha256(f"{prefix}|{normalized}".encode("utf-8")).hexdigest()[:32]


def maturity_for(*, outcome_status: str | None, evidence_sufficient: bool, effect_size: float | None = None, min_effect: float = 0.0, prior_successes: int = 0) -> str:
    """Explore → Validate → Scale; insufficient evidence never advances maturity."""
    status = str(outcome_status or "INCONCLUSIVE").upper()
    if status in {"FAIL", "REJECTED", "DISPROVEN"}:
        return "EXPLORE"
    if status != "SUCCESS" or not evidence_sufficient:
        return "EXPLORE" if prior_successes == 0 else "VALIDATE"
    if prior_successes >= 1 and effect_size is not None and effect_size >= min_effect:
        return "SCALE"
    return "VALIDATE"

async def retrieve(db: AsyncSession, *, channel_id: str | None, campaign_id: str | None, object_type: str = "CHANNEL", object_id: str | None = None) -> list[AdsMemory]:
    # Never widen scope across owners. Campaign-specific memories outrank the
    # promotion-object scope, while account/global memories are intentionally
    # not eligible for private retrieval in V1.
    scope = [AdsMemory.scope == "PRIVATE", AdsMemory.status.in_(ACTIVE)]
    if object_type == "CHANNEL":
        scope.append(AdsMemory.channel_id == channel_id)
    else:
        scope.extend([AdsMemory.promotion_object_type == object_type, AdsMemory.promotion_object_id == object_id])
    query = select(AdsMemory).where(*scope)
    if campaign_id:
        query = query.order_by(case((AdsMemory.campaign_id == campaign_id, 0), else_=1), AdsMemory.updated_at.desc())
    else:
        query = query.order_by(AdsMemory.updated_at.desc())
    return list((await db.scalars(query.limit(12))).all())

async def create_candidates(db: AsyncSession, *, channel_id: str | None, campaign_id: str | None, ai_run_id: str, snapshot_id: str, candidates: list[dict], known_evidence: set[str], proven_outcome: bool = False, object_type: str = "CHANNEL", object_id: str | None = None, experiment_id: str | None = None, memory_status: str = "VALIDATED") -> list[AdsMemory]:
    """Persist memory only after a proven closed-loop outcome.

    AI suggestions remain in the validated run payload until an outcome has
    implementation evidence and a causal SUCCESS/FAIL classification.
    """
    if not proven_outcome:
        return []
    created: list[AdsMemory] = []
    for item in candidates:
        statement = str(item.get("statement") or "").strip()
        refs = item.get("source_evidence_ids") or []
        if not statement or not refs or any(ref not in known_evidence for ref in refs): continue
        scope_filter = (AdsMemory.channel_id == channel_id) if object_type == "CHANNEL" else (AdsMemory.promotion_object_type == object_type, AdsMemory.promotion_object_id == object_id)
        key = claim_key(statement, objective=item.get("objective"), action=item.get("action_code"))
        duplicate = await db.scalar(select(AdsMemory).where(*((scope_filter,) if not isinstance(scope_filter, tuple) else scope_filter), AdsMemory.status != "REJECTED", AdsMemory.kind == f"CLAIM:{key}"))
        if duplicate:
            # A conclusive opposite outcome is contradiction evidence, not a
            # reason to delete history. Downgrade the prior claim and append
            # the new evidence to the same canonical claim.
            if memory_status == "REJECTED" and duplicate.status in {"VALIDATED", "TRUSTED"}:
                duplicate.status = "SUPPORTED"
                duplicate.confidence = "MEDIUM"
            existing_refs = json.loads(duplicate.evidence_ids_json or "[]")
            new_refs = [ref for ref in refs if ref not in existing_refs]
            duplicate.evidence_ids_json = json.dumps(list(dict.fromkeys(existing_refs + refs)))
            for ref in new_refs:
                db.add(AdsMemoryEvidence(memory_id=duplicate.id, experiment_id=experiment_id, evidence_type="CONTRADICTION" if memory_status == "REJECTED" else "FACT", evidence_ref_id=ref, snapshot_id=snapshot_id, ai_run_id=ai_run_id))
            continue
        status = memory_status if proven_outcome and memory_status in {"VALIDATED", "REJECTED"} else "INSUFFICIENT_EVIDENCE" if not proven_outcome else "CANDIDATE"
        confidence = "HIGH" if proven_outcome and memory_status == "VALIDATED" else "LOW"
        memory = AdsMemory(channel_id=channel_id, promotion_object_type=object_type, promotion_object_id=object_id, campaign_id=campaign_id, experiment_id=experiment_id, content=statement, evidence_ids_json=json.dumps(refs), source_type="AI_INFERENCE", status=status, kind=f"CLAIM:{key}", confidence=confidence)
        db.add(memory); await db.flush()
        for ref in refs: db.add(AdsMemoryEvidence(memory_id=memory.id, experiment_id=experiment_id, evidence_type="FACT", evidence_ref_id=ref, snapshot_id=snapshot_id, ai_run_id=ai_run_id))
        created.append(memory)
    return created

async def set_status(db: AsyncSession, *, memory_id: str, channel_id: str, status: str) -> bool:
    memory = await db.scalar(select(AdsMemory).where(AdsMemory.id == memory_id, AdsMemory.channel_id == channel_id))
    if not memory or status not in MEMORY_STATUSES: return False
    memory.status = status; await db.commit(); return True
