"""One final, deterministic budget for the complete AI context."""
from __future__ import annotations

import json
from typing import Any


def _bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def bound_ai_context(context: dict[str, Any], *, target_bytes: int, max_bytes: int, max_evidence: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Trim optional context parts while keeping evidence references closed."""
    data = json.loads(json.dumps(context, ensure_ascii=False))
    facts = data.setdefault("FACTS", {})
    v2 = facts.setdefault("V2_ANALYSIS", {})

    def sync_evidence() -> None:
        referenced = set()
        for section in (v2.get("sections") or {}).values():
            for item in section.get("notable", []):
                if item.get("evidence_id"):
                    referenced.add(item["evidence_id"])
        for finding in (v2.get("findings") or {}).get("positive", []) + (v2.get("findings") or {}).get("watch", []):
            referenced.update(finding.get("evidence_ids") or [])
        for finding in (facts.get("TRENDS") or {}).get("findings", []):
            referenced.update(finding.get("evidence_ids") or [])
        evidence = v2.get("evidence") or {}
        v2["evidence"] = {key: value for key, value in evidence.items() if key in referenced}

    def trim_one() -> bool:
        memories = data.get("RETRIEVED_PRIVATE_MEMORIES") or []
        if memories:
            memories.pop()
            return True
        for key in ("MEASURED_OUTCOMES", "PREVIOUS_RECOMMENDATIONS"):
            rows = facts.get(key) or []
            if rows:
                rows.pop()
                return True
        for name in ("criteria", "demographics", "device", "region", "placement", "ad", "ad_group"):
            section = (v2.get("sections") or {}).get(name) or {}
            rows = section.get("notable") or []
            if rows:
                rows.pop()
                selection = (v2.get("selection") or {}).get(name) or {}
                selection["rows_included"] = len(rows)
                selection["rows_excluded"] = max(0, int(selection.get("rows_available", len(rows))) - len(rows))
                selection["truncated"] = True
                return True
        return False

    sync_evidence()
    while _bytes(data) > target_bytes and trim_one():
        sync_evidence()
    final_bytes = _bytes(data)
    report = {
        "target_bytes": target_bytes,
        "max_bytes": max_bytes,
        "bytes": final_bytes,
        "evidence": len(v2.get("evidence") or {}),
        "max_evidence": max_evidence,
        "budget_exceeded": final_bytes > max_bytes or len(v2.get("evidence") or {}) > max_evidence,
        "trimming_applied": final_bytes < _bytes(context),
    }
    if report["budget_exceeded"]:
        raise ValueError("AI_CONTEXT_BUDGET_EXCEEDED")
    return data, report
