from app.ads.evidence_policy import (
    ALPHA, HARD_MIN_CLICKS, HARD_MIN_EXACT_OUTCOMES, POLICY_VERSION, POWER,
    build_evidence_policy, evaluate_policy_state,
)


def test_policy_is_deterministic_and_hashed():
    kwargs = dict(baseline={"clicks": 100, "exact_subscribers": 10, "spend": 250}, stage="EXPLORE", action_type="BOUNDED_BUDGET_REALLOCATION", snapshot_id="snap-1")
    first = build_evidence_policy(**kwargs)
    second = build_evidence_policy(**kwargs)
    assert first == second
    assert first["version"] == POLICY_VERSION
    assert first["alpha"] == ALPHA and first["power"] == POWER
    assert first["policy_hash"]


def test_hard_floors_and_maturity_are_monotonic():
    explore = build_evidence_policy(baseline={"clicks": 100, "exact_subscribers": 10}, stage="EXPLORE")
    validate = build_evidence_policy(baseline={"clicks": 100, "exact_subscribers": 10}, stage="VALIDATE")
    scale = build_evidence_policy(baseline={"clicks": 100, "exact_subscribers": 10}, stage="SCALE")
    assert explore["required_sample"] >= HARD_MIN_CLICKS
    assert explore["required_exact_outcomes"] >= HARD_MIN_EXACT_OUTCOMES
    assert validate["required_sample"] >= explore["required_sample"]
    assert scale["required_sample"] >= validate["required_sample"]


def test_unknown_freshness_and_max_duration_fail_closed():
    policy = build_evidence_policy(baseline={"clicks": 100, "exact_subscribers": 10})
    assert evaluate_policy_state(policy=policy, clicks=100, exact_outcomes=10, elapsed_hours=24, freshness_hours=None, attribution_coverage=1.0, data_trust="GOOD") == "EARLY_STOP"
    assert evaluate_policy_state(policy=policy, clicks=100, exact_outcomes=10, elapsed_hours=169, freshness_hours=1, attribution_coverage=1.0, data_trust="GOOD") == "INCONCLUSIVE"
    assert evaluate_policy_state(policy=policy, clicks=100, exact_outcomes=10, elapsed_hours=24, freshness_hours=1, attribution_coverage=1.0, data_trust="GOOD", stop_loss=True) == "EARLY_STOP"
