from __future__ import annotations

from dataclasses import dataclass


EXACT = "EXACT"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
AMBIGUOUS = "AMBIGUOUS"
UNATTRIBUTED = "UNATTRIBUTED"


def resolve_exact_identity_visit(visit_ids: list[str]) -> str | None:
    """Return a visit only when the MAX identity maps to exactly one visit."""
    unique = {str(value) for value in visit_ids if value}
    return next(iter(unique)) if len(unique) == 1 else None

SINGLE_CANDIDATE = HIGH
PROBABLE = MEDIUM


@dataclass(frozen=True)
class AttributionDecision:
    status: str
    visit_id: str | None = None
    score: int | None = None
    reason: str = ""


def decide(candidate_count: int, best_visit_id: str | None = None, best_score: int | None = None) -> AttributionDecision:
    """Resolve only evidence-safe states.

    A single temporal candidate is deliberately not promoted to EXACT.  EXACT
    is reserved for a future provider that supplies a deterministic MAX-side
    source identifier.
    """
    if candidate_count <= 0:
        return AttributionDecision(UNATTRIBUTED, reason="no_matching_visit")
    if candidate_count == 1:
        return AttributionDecision(HIGH, best_visit_id, best_score, "single_candidate_in_join_window")
    return AttributionDecision(AMBIGUOUS, None, best_score, "multiple_temporal_candidates")


def can_send_offline_conversion(status: str, identifier: str | None) -> bool:
    """Allow delivery only for a deterministic MAX identity binding.

    A temporal candidate is useful for owner diagnostics, but it cannot prove
    that a particular Yandex click belongs to the MAX member.  In particular,
    ``HIGH`` is a ranking result, not an identity proof.
    """
    return status == EXACT and bool(identifier and identifier.strip())


def score_time_distance(delta_seconds: float, window_seconds: int) -> int:
    if delta_seconds < 0 or delta_seconds > window_seconds:
        return 0
    return max(1, 100 - int((delta_seconds / window_seconds) * 100))


def decide_scored(candidates: list[tuple], window_seconds: int) -> AttributionDecision:
    if not candidates:
        return AttributionDecision(UNATTRIBUTED, reason="no_matching_visit")
    # v2 accepts both v1 tuples (visit_id, delta_seconds) and
    # (visit_id, delta_seconds, max_open_likely_score).  The open signal is a
    # ranking feature only; it is never treated as deterministic identity.
    enriched = [(item[0], item[1], float(item[2]) if len(item) > 2 else 0.0) for item in candidates]
    scored = [
        (visit_id, score_time_distance(delta, window_seconds) + int(max(0.0, min(open_score, 1.0)) * 25), open_score)
        for visit_id, delta, open_score in enriched
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    if len(scored) == 1:
        reason = "single_candidate_with_open_signal" if scored[0][2] > 0 else "single_candidate_in_join_window"
        return AttributionDecision(HIGH, scored[0][0], scored[0][1], reason)
    lead = scored[0][1] - scored[1][1]
    if lead >= 20:
        open_count = sum(1 for _, _, signal in scored if signal > 0)
        if open_count == 1:
            reason = "multiple_candidates_only_one_open_signal"
        elif open_count > 1:
            reason = "multiple_open_candidates"
        else:
            reason = "best_temporal_candidate_with_alternatives"
        return AttributionDecision(MEDIUM, scored[0][0], scored[0][1], reason)
    if any(signal > 0 for _, _, signal in scored):
        reason = "multiple_open_candidates" if sum(signal > 0 for _, _, signal in scored) > 1 else "multiple_candidates_only_one_open_signal"
    else:
        reason = "no_open_signal"
    return AttributionDecision(AMBIGUOUS, None, scored[0][1], reason)
