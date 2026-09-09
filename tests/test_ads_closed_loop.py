from datetime import datetime, timedelta, timezone

import pytest

from app.ads.context_budget import bound_ai_context
from app.ads.llm import SYSTEM_PROMPT
from app.ads.outcome_collector import collect_one_outcome
from app.ads.outcomes import (
    OUTCOME_HORIZONS,
    ensure_outcome_checkpoints,
    outcome_metrics_sufficient,
    classify_outcome,
    evaluate_success_criteria,
)
from app.db.models import AdsRecommendation, AdsV2Outcome


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self):
        self.outcomes = []
        self.flushed = 0

    async def scalars(self, _statement):
        return _Scalars(self.outcomes)

    def add(self, row):
        if isinstance(row, AdsV2Outcome):
            self.outcomes.append(row)

    async def flush(self):
        self.flushed += 1


def _known_facts():
    return {key: 0 for key in ("impressions_7d", "clicks_7d", "cost_7d", "conversions_7d")}


def test_checkpoint_horizons_are_deterministic():
    assert tuple(h for h, _ in OUTCOME_HORIZONS) == ("24h", "72h", "7d")


def test_known_zero_metrics_are_sufficient_but_missing_are_not():
    assert outcome_metrics_sufficient(_known_facts())
    assert not outcome_metrics_sufficient({**_known_facts(), "clicks_7d": None})
    assert outcome_metrics_sufficient({"facts": _known_facts()})


@pytest.mark.asyncio
async def test_checkpoint_creation_is_idempotent_and_uses_created_at():
    db = _Session()
    created_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    recommendation = AdsRecommendation(id="rec-1", created_at=created_at)

    assert await ensure_outcome_checkpoints(db, recommendation, baseline_snapshot_id="snap-1") == 3
    assert await ensure_outcome_checkpoints(db, recommendation, baseline_snapshot_id="snap-2") == 0
    assert {row.horizon for row in db.outcomes} == {"24h", "72h", "7d"}
    assert all(row.due_at == created_at + delta for row, (_, delta) in zip(sorted(db.outcomes, key=lambda x: x.due_at), OUTCOME_HORIZONS))
    assert all(row.execution_status == "RECOMMENDATION_ONLY" for row in db.outcomes)


def test_closed_loop_context_is_bounded_and_preserves_pending_rows():
    context = {
        "FACTS": {
            "PREVIOUS_RECOMMENDATIONS": [{"id": str(i), "execution_status": "RECOMMENDATION_ONLY"} for i in range(5)],
            "MEASURED_OUTCOMES": [{"horizon": "24h", "availability": "PENDING"} for _ in range(15)],
            "V2_ANALYSIS": {"evidence": {str(i): {"x": "y"} for i in range(10)}},
        },
    }
    bounded, report = bound_ai_context(context, target_bytes=5000, max_bytes=5000, max_evidence=10)
    assert bounded["FACTS"]["MEASURED_OUTCOMES"][-1]["availability"] == "PENDING"
    assert report["bytes"] <= 5000


def test_prompt_contract_separates_execution_pending_and_causality():
    for phrase in ("RECOMMENDATION_ONLY", "pending", "completed", "causality"):
        assert phrase.lower() in SYSTEM_PROMPT.lower()


def test_membership_context_does_not_claim_campaign_specific_causality():
    assert "campaign-specific" in SYSTEM_PROMPT.lower()
    assert "causal" in SYSTEM_PROMPT.lower()


def test_outcome_rows_are_observations_not_campaign_totals():
    assert "observed after" in SYSTEM_PROMPT.lower()
    assert "do not" in SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_due_guard_prevents_early_collection():
    class _NeverUsed:
        async def get(self, *_args):
            raise AssertionError("future outcome must not reach provider flow")

    outcome = AdsV2Outcome(due_at=datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(ValueError, match="OUTCOME_NOT_DUE"):
        await collect_one_outcome(_NeverUsed(), outcome)


def test_pending_is_not_completed():
    assert "pending or not-available outcomes from completed measurements" in SYSTEM_PROMPT.lower()


def test_unknown_is_not_zero():
    assert "unknown is not zero" in SYSTEM_PROMPT.lower()


def test_no_causality_claim():
    assert "do not prove causality" in SYSTEM_PROMPT.lower()


def test_recommendations_are_advisory():
    assert "never propose execution" in SYSTEM_PROMPT.lower()


def test_causality_gate_keeps_unimplemented_observation_inconclusive():
    assert classify_outcome(implementation_confirmed="UNKNOWN", valid_window=True, sufficient_data=True, success_criteria_met=True) == "INCONCLUSIVE"
    assert classify_outcome(implementation_confirmed="YES", valid_window=True, sufficient_data=True, success_criteria_met=True) == "SUCCESS"
    assert classify_outcome(implementation_confirmed="YES", valid_window=True, sufficient_data=True, success_criteria_met=False) == "FAIL"


def test_outcome_horizon_due_offsets_are_fixed():
    assert [delta.total_seconds() for _, delta in OUTCOME_HORIZONS] == [86400, 259200, 604800]


def test_context_has_separate_history_sections():
    assert "PREVIOUS_RECOMMENDATIONS" in SYSTEM_PROMPT
    assert "MEASURED_OUTCOMES" in SYSTEM_PROMPT


def test_explicit_success_criteria_are_deterministic_and_conservative():
    spec = {"metric": "cpa_7d", "direction": "decrease", "threshold": 0.10}
    assert evaluate_success_criteria(expected_impact=spec, delta={"cpa_7d": {"before": 100, "after": 85}}) is True
    assert evaluate_success_criteria(expected_impact=spec, delta={"cpa_7d": {"before": 100, "after": 95}}) is False
    assert evaluate_success_criteria(expected_impact=spec, delta={"cpa_7d": {"before": 0, "after": 0}}) is None
    assert evaluate_success_criteria(expected_impact={}, delta={"cpa_7d": {"before": 100, "after": 50}}) is None


def test_recommendation_only_and_insufficient_data_cannot_be_verified():
    assert classify_outcome(implementation_confirmed="UNKNOWN", valid_window=True, sufficient_data=True, success_criteria_met=True) == "INCONCLUSIVE"
    assert classify_outcome(implementation_confirmed="YES", valid_window=True, sufficient_data=False, success_criteria_met=True) == "INCONCLUSIVE"
