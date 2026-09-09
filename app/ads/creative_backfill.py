"""Read-only planning for creative media backfill; never mutates production."""
from __future__ import annotations

from collections import Counter
from typing import Iterable


def build_backfill_plan(rows: Iterable[dict]) -> dict[str, object]:
    counts = Counter()
    for row in rows:
        if row.get("image_ref") or row.get("video_ref"):
            counts["source_references"] += 1
            counts["source_unavailable"] += 1
        else:
            counts["metadata_only"] += 1
    return {
        "mode": "DRY_RUN",
        "would_upload": 0,
        "would_update_rows": 0,
        "source_references": counts["source_references"],
        "source_unavailable": counts["source_unavailable"],
        "metadata_only": counts["metadata_only"],
        "mutations_performed": 0,
        "reason": "Provider bytes are not available in the current collector; no fabricated media is created.",
    }
