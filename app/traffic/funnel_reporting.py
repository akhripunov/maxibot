"""Safe aggregation for promotion-funnel reporting.

The reporting surface deliberately receives raw yclid values only long enough
to de-duplicate them.  It never returns an identifier, token, user id, URL or
user agent to an owner-facing diagnostic response.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


STAGE_ORDER = {
    "LANDING_ONLY": 0,
    "MAX_LINK_ASSIGNED": 1,
    "MINIAPP_LOADED": 2,
    "IDENTITY_BOUND": 3,
    "CHANNEL_HANDOFF_STARTED": 4,
}


@dataclass(frozen=True)
class FunnelObservation:
    """One persisted landing request with its safe event-type set."""

    yclid: str | None
    utm_campaign: str | None
    stages: frozenset[str]


def campaign_matches(value: str | None, requested_campaign: str | None) -> bool:
    """Match exact IDs and MAXIBOT UTM component/suffix forms."""
    if not requested_campaign:
        return True
    candidate = (value or "").strip()
    requested = requested_campaign.strip()
    if not requested:
        return False
    return bool(
        candidate == requested
        or candidate.startswith(f"{requested}.")
        or requested in candidate.split(".")
    )


def deepest_stage(stages: Iterable[str]) -> str:
    values = set(stages)
    if "MINIAPP_CHANNEL_HANDOFF_STARTED" in values:
        return "CHANNEL_HANDOFF_STARTED"
    if "max_identity_linked" in values:
        return "IDENTITY_BOUND"
    if values.intersection({"MINI_APP_LOADED", "HANDOFF_CONFIRMED", "RESOLVE_OK"}):
        return "MINIAPP_LOADED"
    if "MAX_LINK_ASSIGNED" in values:
        return "MAX_LINK_ASSIGNED"
    return "LANDING_ONLY"


def canonical_paid_click_report(
    observations: Iterable[FunnelObservation], *, requested_campaign: str | None = None
) -> dict[str, int | dict[str, int]]:
    """Return transparent counts for requests vs canonical unique yclids.

    A yclid is only considered a campaign-paid click for this report when its
    visit carries the requested campaign (if supplied).  Repeated requests
    with the same yclid remain visible in ``duplicate_or_revisit_requests``.
    """
    rows = list(observations)
    matched = [row for row in rows if campaign_matches(row.utm_campaign, requested_campaign)]
    yclid_rows = [row for row in matched if row.yclid and row.yclid.strip()]
    by_yclid: dict[str, set[str]] = {}
    for row in yclid_rows:
        by_yclid.setdefault(row.yclid.strip(), set()).update(row.stages)
    deepest = {stage: 0 for stage in STAGE_ORDER}
    for stages in by_yclid.values():
        deepest[deepest_stage(stages)] += 1
    return {
        "raw_landing_requests": len(rows),
        "campaign_matched_raw_requests": len(matched),
        "campaign_matched_missing_yclid_requests": len(matched) - len(yclid_rows),
        "direct_attributed_raw_yclid_requests": len(yclid_rows),
        "direct_attributed_unique_yclid": len(by_yclid),
        "duplicate_or_revisit_requests": len(yclid_rows) - len(by_yclid),
        "other_or_unmatched_requests": len(rows) - len(matched),
        "deepest_stage_by_unique_yclid": deepest,
    }
