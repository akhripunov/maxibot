"""Deterministic, fail-closed experience transfer and prior engine (V1).

The engine is deliberately pure/read-only: it ranks applicable evidence but
cannot create memory, change maturity, or authorize provider actions.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

CONTEXT_SIGNATURE_VERSION = "experiment-context-v1"
SIMILARITY_POLICY_VERSION = "similarity-v1"
SCORING_POLICY_VERSION = "prior-score-v1"
DECAY_POLICY_VERSION = "half-life-180d-v1"

CONTEXT_FIELDS = (
    "provider", "destination_platform", "promotion_object_type", "market", "region",
    "language", "currency", "campaign_type", "provider_strategy", "optimization_goal",
    "payment_model", "network_context", "business_objective", "primary_metric",
    "target_cpa_band", "weekly_budget_band", "test_spend_band", "baseline_cpa_band",
    "baseline_cvr_band", "baseline_ctr_band", "baseline_cpc_band", "traffic_sample_band",
    "maturity_stage", "action_type", "creative_category", "device_mix", "placement_mix",
    "data_trust", "attribution_quality", "experiment_policy_version",
)
WEIGHTS = {field: 2.0 for field in CONTEXT_FIELDS}
for field in ("provider", "destination_platform", "promotion_object_type", "campaign_type", "provider_strategy", "business_objective", "primary_metric", "action_type"):
    WEIGHTS[field] = 4.0


def _norm(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return " ".join(value.strip().upper().split())
    if isinstance(value, (list, tuple, set)):
        return sorted((_norm(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, Mapping):
        return {str(k): _norm(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    return value


def build_context_signature(context: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical, versioned signature. Unknowns remain explicit and never match."""
    values = {field: _norm(context.get(field)) for field in CONTEXT_FIELDS}
    payload = {"version": CONTEXT_SIGNATURE_VERSION, "fields": values}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    payload["hash"] = hashlib.sha256(encoded).hexdigest()
    return payload


@dataclass(frozen=True)
class Similarity:
    score: float
    matched: tuple[str, ...]
    mismatched: tuple[str, ...]
    unknown: tuple[str, ...]
    applicable: bool


def similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> Similarity:
    l, r = left.get("fields", left), right.get("fields", right)
    matched, mismatched, unknown = [], [], []
    total = earned = 0.0
    for field in CONTEXT_FIELDS:
        a, b = _norm(l.get(field)), _norm(r.get(field))
        if a is None or b is None:
            unknown.append(field)
            continue
        weight = WEIGHTS[field]
        total += weight
        if a == b:
            earned += weight; matched.append(field)
        else:
            mismatched.append(field)
    return Similarity(round(earned / total, 8) if total else 0.0, tuple(matched), tuple(mismatched), tuple(unknown), bool(total))


def _age_factor(item: Mapping[str, Any], now: datetime) -> float:
    raw = item.get("observed_at") or item.get("created_at")
    if not raw:
        return 0.0
    try:
        when = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if when.tzinfo is None: when = when.replace(tzinfo=timezone.utc)
        days = max(0.0, (now - when).total_seconds() / 86400)
        return math.pow(0.5, days / 180.0)
    except (TypeError, ValueError):
        return 0.0


def aggregate_evidence(evidence: Iterable[Mapping[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Deduplicate experiments and exclude technical canaries."""
    now = now or datetime.now(timezone.utc)
    by_experiment: dict[str, Mapping[str, Any]] = {}
    for item in evidence:
        if str(item.get("experiment_kind", "")).upper() == "TECHNICAL":
            continue
        key = str(item.get("experiment_id") or item.get("id") or "")
        if not key:
            continue
        previous = by_experiment.get(key)
        if previous is None or str(item.get("observed_at", "")) > str(previous.get("observed_at", "")):
            by_experiment[key] = item
    items = list(by_experiment.values())
    counts = {name: sum(1 for item in items if str(item.get("outcome", "INCONCLUSIVE")).upper() == name) for name in ("SUCCESS", "FAIL", "INCONCLUSIVE")}
    contexts = {str(item.get("context_hash")) for item in items if item.get("context_hash")}
    clients = {str(item.get("client_id")) for item in items if item.get("client_id")}
    cohorts = {str(item.get("cohort_id")) for item in items if item.get("cohort_id")}
    return {"experiments": len(items), "success": counts["SUCCESS"], "fail": counts["FAIL"], "inconclusive": counts["INCONCLUSIVE"], "independent_contexts": len(contexts), "independent_clients": len(clients), "independent_cohorts": len(cohorts), "items": items}


def promotion_scope(summary: Mapping[str, Any], *, tenant_verified: bool = True) -> str:
    if not tenant_verified:
        return "NONE"
    n, c, clients = summary.get("experiments", 0), summary.get("independent_contexts", 0), summary.get("independent_clients", 0)
    if n >= 30 and c >= 10 and summary.get("independent_cohorts", 0) >= 3:
        return "GLOBAL"
    if n >= 10 and c >= 5:
        return "COHORT"
    if n >= 5 and c >= 3 and clients >= 2:
        return "COHORT_CANDIDATE"
    return "LOCAL"


def rank_priors(candidates: Iterable[Mapping[str, Any]], target_signature: Mapping[str, Any], *, tenant_id: str | None, flags: Mapping[str, bool] | None = None) -> list[dict[str, Any]]:
    """Stable ranking; external/cross-tenant data is never promoted implicitly."""
    flags = flags or {}
    result = []
    for item in candidates:
        if item.get("tenant_id") not in (tenant_id, None) and str(item.get("scope", "")).upper() != "EXTERNAL":
            continue
        if str(item.get("scope", "")).upper() == "EXTERNAL" and not flags.get("external", False):
            continue
        if not item.get("tenant_verified", item.get("tenant_id") == tenant_id):
            continue
        sig = item.get("context_signature") or {}
        sim = similarity(target_signature, sig)
        if not sim.applicable:
            continue
        summary = aggregate_evidence(item.get("evidence", []))
        scope = promotion_scope(summary, tenant_verified=True)
        local = 1.0 if item.get("tenant_id") == tenant_id else 0.0
        score = round(sim.score * (0.7 + 0.3 * local) * _age_factor(item, datetime.now(timezone.utc)), 8)
        result.append({"id": item.get("id"), "scope": scope, "score": score, "similarity": sim, "evidence": summary})
    return sorted(result, key=lambda row: (-row["score"], str(row["id"] or "")))


def replay(candidates: Iterable[Mapping[str, Any]], target_signature: Mapping[str, Any], *, tenant_id: str | None, flags: Mapping[str, bool] | None = None) -> dict[str, Any]:
    """Offline/shadow evaluation with no persistence or provider side effects."""
    return {"policy_version": SCORING_POLICY_VERSION, "ranked": rank_priors(candidates, target_signature, tenant_id=tenant_id, flags=flags)}
