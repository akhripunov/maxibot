"""Deterministic hypothesis maturity for the autonomous decision loop.

Maturity is derived from evidence and is deliberately separate from an
individual Experiment #N stored in ads_recommendations.
"""
from __future__ import annotations

from typing import Any

from app.ads.memory import maturity_for

EXPLORE, VALIDATE, SCALE = "EXPLORE", "VALIDATE", "SCALE"


def hypothesis_identity(*, scope: str, objective: str, action: str, dimension: str, concept: str = "") -> str:
    from app.ads.memory import claim_key
    return claim_key(f"{dimension}:{concept}", objective=objective, action=f"{scope}:{action}")


def evaluate_maturity(*, outcomes: list[dict[str, Any]], target_cpa: float | None,
                     attribution_quality: str, data_health: str, budget_available: bool,
                     min_effect: float = 0.05) -> dict[str, Any]:
    """Return state plus explainable gates; unknown critical inputs fail closed."""
    successes = [row for row in outcomes if str(row.get("status", "")).upper() == "SUCCESS"]
    latest = outcomes[-1] if outcomes else {}
    evidence = bool(latest.get("evidence_sufficient"))
    effect = latest.get("effect_size")
    state = maturity_for(outcome_status=latest.get("status"), evidence_sufficient=evidence,
                         effect_size=float(effect) if effect is not None else None,
                         min_effect=min_effect, prior_successes=len(successes[:-1]))
    gates = {
        "target_cpa": target_cpa is not None,
        "attribution": str(attribution_quality).upper() in {"KNOWN", "PARTIAL"},
        "data_health": str(data_health).upper() == "HEALTHY",
        "budget": bool(budget_available),
        "evidence": evidence,
        "minimum_effect": effect is not None and abs(float(effect)) >= min_effect,
    }
    if state == SCALE and not all(gates.values()):
        state = VALIDATE
    return {"state": state, "gates": gates, "successes": len(successes), "outcomes": len(outcomes)}
