"""Closed-Loop V1 service-path acceptance tests on an isolated database."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ads.memory import retrieve
from app.ads.outcomes import ensure_outcome_checkpoints, mark_outcome_completed, record_implementation_evidence
from app.ads.service import _active_experiment_exists, _assign_experiment_identity, _closed_loop_context, _context, _experiment_object_lock_key, _experiment_scope_lock_key
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
    # A confirmed FAIL is durable audit evidence, but must not be reused as a
    # positive learning signal for the next recommendation.
    assert len(memories) == 1
    assert all(item.status == "VALIDATED" for item in memories)
    context = await _closed_loop_context(db, object_type="BOT", object_id="bot-e2e", campaign_id="campaign-e2e")
    assert context["LEARNING_STATE"]["status"] == "AVAILABLE"
    assert {item["evaluation_status"] for item in context["LEARNING_STATE"]["verified_outcomes"]} == {"SUCCESS", "FAIL"}
    engine_context = _context(None, {"V2_ANALYSIS": {}}, "SUFFICIENT", [], memories)
    assert len(engine_context["RETRIEVED_PRIVATE_MEMORIES"]) == 1
    assert all(item["evidence_ids"] == ["FACT-001"] for item in engine_context["RETRIEVED_PRIVATE_MEMORIES"])
    assert all(item["status"] != "CANDIDATE" for item in engine_context["RETRIEVED_PRIVATE_MEMORIES"])


@pytest.mark.asyncio
async def test_repeated_promotion_is_idempotent(db):
    _, outcome = await _run(db, expected_after=13, evidence=True)
    await mark_outcome_completed(db, outcome, followup_snapshot_id=outcome.followup_snapshot_id, delta={key: {"before": 10, "after": 13} for key in ("impressions_7d", "clicks_7d", "cost_7d", "conversions_7d")})
    assert await db.scalar(select(func.count(AdsAiOutcome.id))) == 3
    assert await db.scalar(select(func.count(AdsMemory.id))) == 1
    assert await db.scalar(select(func.count(AdsMemoryEvidence.id))) == 1


@pytest.mark.asyncio
async def test_first_experiment_identity_needs_no_preexisting_open_recommendation(db):
    ai = AdsAiRun(id="ai-first", promotion_object_type="BOT", promotion_object_id="bot-first", campaign_id="campaign-first")
    recommendation = AdsRecommendation(id="rec-first", ai_run_id=ai.id, promotion_object_type="BOT", promotion_object_id="bot-first", title="first")
    db.add_all([ai, recommendation])
    await _assign_experiment_identity(db, recommendation)
    assert recommendation.experiment_id == recommendation.id
    assert recommendation.experiment_seq == 1
    assert recommendation.experiment_status == "planned"
    assert not await _active_experiment_exists(db, object_type="BOT", object_id="bot-first", campaign_id="campaign-first")


@pytest.mark.asyncio
async def test_business_experiment_sequence_ignores_technical_and_recommendation_rows(db):
    ai = AdsAiRun(id="ai-sequence", promotion_object_type="BOT", promotion_object_id="bot-sequence", campaign_id="campaign-sequence")
    implemented = AdsRecommendation(
        id="rec-business-1", ai_run_id=ai.id, promotion_object_type="BOT", promotion_object_id="bot-sequence",
        experiment_id="business-1", experiment_seq=1, experiment_status="completed", title="Executed business test",
    )
    technical = AdsRecommendation(
        id="rec-technical", promotion_object_type="BOT", promotion_object_id="bot-sequence",
        experiment_id="canary", experiment_seq=100, experiment_kind="TECHNICAL_CANARY", title="Canary",
    )
    recommendations = [
        AdsRecommendation(id=f"rec-hold-{n}", ai_run_id=ai.id, promotion_object_type="BOT",
                          promotion_object_id="bot-sequence", experiment_id=f"hold-{n}",
                          experiment_seq=2, action_code="HOLD", title="Recommendation only")
        for n in range(2)
    ]
    next_ai = AdsAiRun(id="ai-sequence-next", promotion_object_type="BOT", promotion_object_id="bot-sequence", campaign_id="campaign-sequence")
    next_recommendation = AdsRecommendation(
        id="rec-business-next", ai_run_id=next_ai.id, promotion_object_type="BOT",
        promotion_object_id="bot-sequence", title="Next executed test",
    )
    db.add_all([ai, next_ai, implemented, technical, *recommendations, next_recommendation])
    await db.flush()
    db.add_all([
        AdsAiOutcome(recommendation_id=implemented.id, experiment_id=implemented.experiment_id,
                     horizon="7d", due_at=datetime.now(timezone.utc), implementation_confirmed="YES",
                     execution_status="IMPLEMENTED"),
        AdsAiOutcome(recommendation_id=technical.id, experiment_id=technical.experiment_id,
                     horizon="7d", due_at=datetime.now(timezone.utc), implementation_confirmed="YES",
                     execution_status="ROLLED_BACK"),
        *(AdsAiOutcome(recommendation_id=item.id, experiment_id=item.experiment_id,
                       horizon="7d", due_at=datetime.now(timezone.utc), implementation_confirmed="UNKNOWN",
                       execution_status="RECOMMENDATION_ONLY") for item in recommendations),
    ])
    await db.flush()

    await _assign_experiment_identity(db, next_recommendation)

    assert next_recommendation.experiment_seq == 2
    assert next_recommendation.experiment_id == next_recommendation.id


@pytest.mark.asyncio
async def test_active_experiment_blocks_second_experiment_but_completed_allows_next(db):
    ai = AdsAiRun(id="ai-active", promotion_object_type="BOT", promotion_object_id="bot-loop", campaign_id="campaign-loop")
    active = AdsRecommendation(id="rec-active", ai_run_id=ai.id, promotion_object_type="BOT", promotion_object_id="bot-loop", title="active", experiment_status="running")
    db.add_all([ai, active])
    await db.flush()
    assert await _active_experiment_exists(db, object_type="BOT", object_id="bot-loop", campaign_id="campaign-loop")
    active.experiment_status = "completed"
    await db.flush()
    assert not await _active_experiment_exists(db, object_type="BOT", object_id="bot-loop", campaign_id="campaign-loop")


def test_experiment_lock_keys_are_stable_and_scope_separated():
    first = _experiment_scope_lock_key(object_type="CHANNEL", object_id="object-1", campaign_id="campaign-1")
    same = _experiment_scope_lock_key(object_type="CHANNEL", object_id="object-1", campaign_id="campaign-1")
    other_campaign = _experiment_scope_lock_key(object_type="CHANNEL", object_id="object-1", campaign_id="campaign-2")
    other_object = _experiment_object_lock_key(object_type="CHANNEL", object_id="object-2")
    assert first == same
    assert first != other_campaign
    assert -2**63 <= first < 2**63
    assert -2**63 <= other_object < 2**63


def test_experiment_creation_source_locks_before_sequence_allocation():
    source = open("app/ads/service.py", encoding="utf-8").read()
    lock_pos = source.index("await _lock_experiment_scope(", source.index("async def _assign_experiment_identity"))
    seq_pos = source.index("func.max(AdsRecommendation.experiment_seq)", lock_pos)
    assert lock_pos < seq_pos
    assert "pg_advisory_xact_lock" in source


def test_enable_path_invokes_decision_runner_and_control_plane_is_write_gated():
    runtime = open("app/bot/runtime.py", encoding="utf-8").read()
    periodic = open("app/ads/periodic.py", encoding="utf-8").read()
    service = open("app/ads/service.py", encoding="utf-8").read()
    assert "await set_mode(db, object_type=object_type, object_id=object_id," in runtime
    assert "mode=AUTONOMOUS, campaign_id=campaign_id" in runtime
    assert "await run_check(db" in runtime and "await run_bot_check(db" in runtime
    assert "await trigger_autopilot_execution(session_factory)" in periodic
    assert "execute_autonomous_action(" in periodic
    assert "_assign_experiment_identity(db, recommendation)" not in service
    assert "assign_business_experiment_identity" in periodic or "assign_business_experiment_identity" in open("app/ads/autopilot.py", encoding="utf-8").read()
    assert "ads_write_actions_enabled" in periodic
