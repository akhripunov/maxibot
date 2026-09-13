"""Deterministic, immutable evidence planning for business experiments."""
from __future__ import annotations

import hashlib
import json
import math
from statistics import NormalDist
from typing import Any

POLICY_VERSION = "evidence_v1"
ALPHA = 0.05
POWER = 0.80
HARD_MIN_CLICKS = 10
HARD_MIN_EXACT_OUTCOMES = 3
MAX_FRESHNESS_HOURS = 48
MIN_ATTRIBUTION_COVERAGE = 0.80
MDE_BY_STAGE = {"EXPLORE": 0.30, "VALIDATE": 0.20, "SCALE": 0.10}

def _sample_size(baseline_rate: float, relative_mde: float, alpha: float = ALPHA, power: float = POWER) -> int:
    """Deterministic normal approximation for a one-arm binary outcome."""
    p = min(max(float(baseline_rate), 1e-6), 1 - 1e-6)
    delta = max(p * float(relative_mde), 1e-6)
    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_power = NormalDist().inv_cdf(power)
    n = ((z_alpha * math.sqrt(2 * p * (1 - p)) + z_power * math.sqrt(p * (1 - p) + (p + delta) * (1 - p - delta))) / delta) ** 2
    return max(1, math.ceil(n))

def build_evidence_policy(*, baseline: dict[str, Any] | None, stage: str = "EXPLORE", experiment_type: str = "LEARNING", primary_metric: str = "exact_subscriber", action_type: str | None = None, snapshot_id: str | None = None, hard_min_clicks: int = HARD_MIN_CLICKS, hard_min_outcomes: int = HARD_MIN_EXACT_OUTCOMES) -> dict[str, Any]:
    """Build a fully serializable policy; missing baseline is never fabricated."""
    stage = str(stage or "EXPLORE").upper()
    if stage not in MDE_BY_STAGE:
        stage = "EXPLORE"
    facts = baseline or {}
    clicks = facts.get("clicks")
    outcomes = facts.get("exact_subscribers", facts.get("confirmed_business_outcomes"))
    spend = facts.get("spend", facts.get("spend_rub"))
    rate = None
    if clicks not in (None, 0) and outcomes is not None:
        rate = max(0.000001, min(0.999999, float(outcomes) / float(clicks)))
    mde = MDE_BY_STAGE[stage]
    calculated = _sample_size(rate, mde) if rate is not None else None
    required_sample = max(int(hard_min_clicks), calculated or int(hard_min_clicks))
    required_outcomes = max(int(hard_min_outcomes), math.ceil((rate or 0) * required_sample))
    traffic_rate = facts.get("eligible_clicks_per_day")
    try:
        traffic_rate = float(traffic_rate) if traffic_rate is not None else None
    except (TypeError, ValueError):
        traffic_rate = None
    expected_days = (required_sample / traffic_rate) if traffic_rate and traffic_rate > 0 else None
    min_duration = 24
    max_duration = max(168, math.ceil(expected_days * 2 * 24)) if expected_days else 168
    policy = {
        "version": POLICY_VERSION, "experiment_type": experiment_type, "maturity_stage": stage,
        "primary_metric": primary_metric, "secondary_metrics": ["clicks", "spend", "business_cpa"],
        "baseline": {"clicks": clicks, "exact_outcomes": outcomes, "spend": spend, "conversion_rate": rate},
        "mde": {"relative": mde}, "alpha": ALPHA, "power": POWER,
        "required_sample": required_sample, "required_exact_outcomes": required_outcomes,
        "min_duration_hours": min_duration, "max_duration_hours": max_duration, "conversion_delay_hours": 48,
        "conversion_delay_source": "OBSERVED" if facts.get("conversion_delay_hours") is not None else "FALLBACK",
        "required_data_trust": "GOOD", "min_attribution_coverage": MIN_ATTRIBUTION_COVERAGE, "max_freshness_hours": MAX_FRESHNESS_HOURS,
        "evaluation_checkpoints": [0.50, 0.75, 1.00],
        "stopping_rule": {"method": "FIXED_HORIZON_V1", "early_stop_only": ["STOP_LOSS", "DATA_TRUST", "SCOPE"]},
        "stop_loss": {}, "created_from_snapshot_id": snapshot_id,
        "policy_inputs": {"action_type": action_type, "hard_min_clicks": hard_min_clicks, "hard_min_outcomes": hard_min_outcomes},
    }
    canonical = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    policy["policy_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    return policy

def evaluate_policy_state(*, policy: dict[str, Any], clicks: int | None, exact_outcomes: int | None, elapsed_hours: float, freshness_hours: float | None, attribution_coverage: float | None, data_trust: str, stop_loss: bool = False) -> str:
    if stop_loss or str(data_trust).upper() != "GOOD" or freshness_hours is None or freshness_hours > float(policy.get("max_freshness_hours", MAX_FRESHNESS_HOURS)) or attribution_coverage is None or attribution_coverage < float(policy.get("min_attribution_coverage", MIN_ATTRIBUTION_COVERAGE)):
        return "EARLY_STOP"
    if elapsed_hours >= float(policy.get("max_duration_hours", 168)):
        return "INCONCLUSIVE"
    enough = (clicks or 0) >= int(policy.get("required_sample", 10)) and (exact_outcomes or 0) >= int(policy.get("required_exact_outcomes", 3)) and elapsed_hours >= float(policy.get("min_duration_hours", 24))
    # SUCCESS here means the frozen evidence boundary is reached; the caller
    # still applies the experiment's declared success criterion to choose the
    # final SUCCESS versus FAIL outcome.
    return "CONTINUE" if not enough else "SUCCESS"
