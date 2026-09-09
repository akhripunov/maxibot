"""Closed-Loop V1 service-path acceptance tests on an isolated database."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ads.memory import retrieve
from app.ads.outcomes import ensure_outcome_checkpoints, mark_outcome_completed, record_implementation_evidence
from app.ads.service import _closed_loop_context, _context
from app.db.models import AdsAiOutcome, AdsAiRun, AdsAnalysisSnapshot, AdsMemory, AdsMemoryEvidence, AdsRecommendation, Base


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


async def _run(db, *, expected_after: float, evidence: bool, sample: bool = True):
    now = datetime.now(timezone.utc)
    snapshot = AdsAnalysisSnapshot(
        promotion_object_type="BOT", promotion_object_id="bot-e2e", campaign_id="campaign-e2e",
        source="MANUAL_CHECK", facts_json=json.dumps({"facts": {"conversions_7d": 10 if sample else None}}),
    )
    db.add(snapshot)
    await db.flush()
    ai = AdsAiRun(
        id=f"ai-{expected_after}-{evidence}-{sample}", promotion_object_type="BOT", promotion_object_id="bot-e2e",
        campaign_id="campaign-e2e", input_snapshot_id=snapshot.id,
        safe_context_json=json.dumps({"FACTS": {"evidence": {"FACT-001": {}}}}),
        validated_output_json=json.dumps({"memory_candidates": [{"statement": f"Conversion hypothesis outcome {expected_after}", "source_evidence_ids": ["FACT-001"]}]}),
    )
    db.add(ai)
    recommendation = AdsRecommendation(
        id=f"rec-{expected_after}-{evidence}-{sample}", ai_run_id=ai.id,
        promotion_object_type="BOT", promotion_object_id="bot-e2e", title="Test", created_at=now,
        expected_impact_json=json.dumps({"metric": "conversions_7d", "direction": "increase", "threshold": 0.2}),
    )
    db.add(recommendation)
    await db.flush()
    await ensure_outcome_checkpoints(db, recommendation, baseline_snapshot_id=snapshot.id)
    outcome = await db.scalar(select(AdsAiOutcome).where(AdsAiOutcome.recommendation_id == recommendation.id, AdsAiOutcome.horizon == "24h"))
    outcome.due_at = now - timedelta(minutes=1)
    if evidence:
        await record_implementation_evidence(db, outcome, {"evidence_type": "OWNER_CONFIRMATION", "reference": "test-run"})
    after = expected_after if sample else None
    delta = {key: {"before": 10, "after": after} for key in ("impressions_7d", "clicks_7d", "cost_7d", "conversions_7d")}
    await mark_outcome_completed(db, outcome, followup_snapshot_id=snapshot.id, delta=delta)
    return recommendation, outcome


@pytest.mark.asyncio
async def test_closed_loop_success_fail_negative_and_run_n_plus_1(db):
    success_rec, success = await _run(db, expected_after=13, evidence=True)
    assert success.evaluation_status == "SUCCESS"
    assert success.implementation_confirmed == "YES"
    assert await db.scalar(select(func.count(AdsMemory.id))) == 1
    assert await db.scalar(select(func.count(AdsMemoryEvidence.id))) == 1

    fail_rec, failure = await _run(db, expected_after=11, evidence=True)
    assert failure.evaluation_status == "FAIL"
    assert await db.scalar(select(func.count(AdsMemory.id))) == 2

    _, no_implementation = await _run(db, expected_after=13, evidence=False)
    assert no_implementation.evaluation_status == "INCONCLUSIVE"
    _, insufficient = await _run(db, expected_after=13, evidence=True, sample=False)
    assert insufficient.evaluation_status == "INCONCLUSIVE"
    assert await db.scalar(select(func.count(AdsMemory.id))) == 2

    memories = await retrieve(db, channel_id=None, campaign_id="campaign-e2e", object_type="BOT", object_id="bot-e2e")
    assert len(memories) == 2
    assert all(item.status == "VALIDATED" for item in memories)
    context = await _closed_loop_context(db, object_type="BOT", object_id="bot-e2e", campaign_id="campaign-e2e")
    assert context["LEARNING_STATE"]["status"] == "AVAILABLE"
    assert {item["evaluation_status"] for item in context["LEARNING_STATE"]["verified_outcomes"]} == {"SUCCESS", "FAIL"}
    engine_context = _context(None, {"V2_ANALYSIS": {}}, "SUFFICIENT", [], memories)
    assert len(engine_context["RETRIEVED_PRIVATE_MEMORIES"]) == 2
    assert all(item["evidence_ids"] == ["FACT-001"] for item in engine_context["RETRIEVED_PRIVATE_MEMORIES"])
    assert all(item["status"] != "CANDIDATE" for item in engine_context["RETRIEVED_PRIVATE_MEMORIES"])


@pytest.mark.asyncio
async def test_repeated_promotion_is_idempotent(db):
    _, outcome = await _run(db, expected_after=13, evidence=True)
    await mark_outcome_completed(db, outcome, followup_snapshot_id=outcome.followup_snapshot_id, delta={key: {"before": 10, "after": 13} for key in ("impressions_7d", "clicks_7d", "cost_7d", "conversions_7d")})
    assert await db.scalar(select(func.count(AdsAiOutcome.id))) == 3
    assert await db.scalar(select(func.count(AdsMemory.id))) == 1
    assert await db.scalar(select(func.count(AdsMemoryEvidence.id))) == 1
