from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any
import httpx

from app.core.config import settings

PROMPT_VERSION = "campaign_diagnosis_v2_ru"
SCHEMA_VERSION = "campaign_diagnosis_schema_v1"
ACTION_CODES = {"HOLD", "MONITOR", "INVESTIGATE", "TEST", "SCALE_CANDIDATE", "REDUCE_CANDIDATE", "PLACEMENT_REVIEW", "ATTRIBUTION_REVIEW", "CREATIVE_REVIEW", "TARGETING_REVIEW", "OBSERVE", "INCREASE_TARGET_CPA", "DECREASE_TARGET_CPA", "REMOVE_TARGET_CPA_CONSTRAINT", "INCREASE_BUDGET", "DECREASE_BUDGET", "PAUSE_CAMPAIGN", "RESUME_CAMPAIGN", "REVIEW_GOAL", "REVIEW_ATTRIBUTION", "REVIEW_CREATIVE", "REVIEW_TARGETING", "REVIEW_PLACEMENTS", "INSUFFICIENT_DATA"}
EVIDENCE_ID_PATTERN = re.compile(r"(?:FACT-\d{3}|V2-[A-Za-z0-9]+|EVIDENCE-\d+|TREND-[A-Za-z0-9]+|MAXIBOT_QUALIFIED_MEMBERSHIP_RATE)")
SECRET_MARKERS = ("access_token", "refresh_token", "authorization", "api_key", "cookie", "password", "secret", "oauth")

SYSTEM_PROMPT = """Use only supplied context, especially the bounded V2_ANALYSIS dataset and its deterministic findings built from ADS Data Layer V2. FACTS.TRENDS contains deterministic Trend Intelligence V1 features computed from non-overlapping completed daily buckets; explain those features, do not recalculate trends from raw rows. UNKNOWN is not zero. Do not invent metrics or causality. Campaign totals are the source of truth; dimensions explain structure and must never be summed together. Use only findings with sufficient sample and HIGH materiality for strong wording; WEAK_SIGNAL means 'есть сигнал', INSUFFICIENT means no conclusion. FACTS.MAXIBOT_QUALIFIED_MEMBERSHIP_RATE is a visit-to-qualified-membership conversion: numerator qualified_memberships, denominator tracked_landing_visits. Never call it attribution coverage, attribution quality, Yandex conversion confirmation, campaign attribution rate, or matching coverage. Keep campaign-specific matching separate; when campaign_specific_matching is UNKNOWN, say that matching with a specific campaign is unavailable. Separate confidence_axes.performance_confidence (Yandex-reported performance) from confidence_axes.attribution_confidence (confidence in campaign-specific matching only). Low campaign-specific matching confidence must not invalidate healthy Yandex results. Keep Yandex conversions and MaxiBot memberships as separate populations and do not imply that one confirms the other. PREVIOUS_RECOMMENDATIONS and MEASURED_OUTCOMES are bounded historical context: distinguish suggested actions marked RECOMMENDATION_ONLY from executed actions, and distinguish pending or not-available outcomes from completed measurements. Observed after-metrics do not prove causality. Use the deterministic recommendation_quality fields as advisory ranking context: action_ladder, signal_evolution, WHY_THIS, WHY_NOW, EVIDENCE, RISK_IF_IGNORED, NEXT_STEP, and WAIT_CONDITION; do not create extra recommendations from duplicate current, trend, or dimension signals. Rank one primary read-only action and at most 3-5 useful granular insights; HOLD may include insights. Never recommend automatic exclusion or execution. Cite only FACT or V2 evidence IDs present in context. Return JSON only matching campaign_diagnosis_schema_v1. Never propose execution. Write all human-readable fields in Russian: summary, evidence.statement, recommendation.reason, do_not_change, and memory_candidates.statement. Keep diagnosis_code and recommendation.action_code as stable English codes."""

class LLMProvider(ABC):
    @abstractmethod
    async def analyze_campaign(self, context: dict[str, Any]) -> dict[str, Any]: ...

class OpenAICompatibleProvider(LLMProvider):
    async def analyze_campaign(self, context: dict[str, Any]) -> dict[str, Any]:
        if not (settings.ads_llm_api_key and settings.ads_llm_model and settings.ads_llm_base_url): raise RuntimeError("AI_PROVIDER_UNAVAILABLE")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(settings.ads_llm_base_url.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {settings.ads_llm_api_key}"}, json={"model": settings.ads_llm_model, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]})
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)

def provider_from_settings() -> LLMProvider | None:
    if not settings.ads_llm_enabled or not settings.ads_llm_api_key: return None
    if (settings.ads_llm_provider or "").lower() in {"openai", "openai_compatible"}: return OpenAICompatibleProvider()
    return None


def assert_safe_context(context: Any) -> None:
    """Fail closed before a provider call if a credential-shaped field leaks."""
    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                next_path = f"{path}.{key}".lower()
                if any(marker in next_path for marker in SECRET_MARKERS):
                    raise ValueError("SAFE_CONTEXT_SECRET_SCAN_FAILED")
                walk(item, next_path)
        elif isinstance(value, list):
            for item in value: walk(item, path)
        elif isinstance(value, str) and any(marker in value.lower() for marker in ("bearer ", "sk-")):
            raise ValueError("SAFE_CONTEXT_SECRET_SCAN_FAILED")
    walk(context)

def evidence_registry(context: dict[str, Any]) -> set[str]:
    """Return exactly the evidence IDs serialized into this AI context."""
    facts = context.get("FACTS") or {}
    ids = set((facts.get("evidence") or {}).keys())
    v2 = facts.get("V2_ANALYSIS") or {}
    ids.update((v2.get("evidence") or {}).keys())
    for trends in (facts.get("TRENDS") or {}, v2.get("trends") or {}):
        ids.update(item.get("trend_finding_id") for item in trends.get("findings") or [] if item.get("trend_finding_id"))
    if "MAXIBOT_QUALIFIED_MEMBERSHIP_RATE" in facts or "MAXIBOT_QUALIFIED_MEMBERSHIP_RATE" in v2:
        ids.add("MAXIBOT_QUALIFIED_MEMBERSHIP_RATE")
    return {item for item in ids if isinstance(item, str)}


def _is_valid_evidence_id(value: Any) -> bool:
    return isinstance(value, str) and bool(EVIDENCE_ID_PATTERN.fullmatch(value))

def validate_output(value: Any, evidence_ids: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict): return None, "output is not an object"
    required = {"status", "diagnosis_code", "summary", "confidence", "data_sufficiency", "evidence", "recommendation", "do_not_change", "missing_data", "memory_candidates"}
    if not required.issubset(value): return None, "required fields missing"
    if value["status"] not in {"OK", "NO_ACTION_NEEDED", "INSUFFICIENT_DATA"}: return None, "invalid status"
    if value["confidence"] not in {"LOW", "MEDIUM", "HIGH"} or value["data_sufficiency"] not in {"SUFFICIENT", "PARTIAL", "INSUFFICIENT"}: return None, "invalid confidence or sufficiency"
    rec = value["recommendation"]
    if not isinstance(rec, dict) or rec.get("action_code") not in ACTION_CODES: return None, "invalid action code"
    evidence = value["evidence"]
    ids = [item.get("evidence_id") for item in evidence if isinstance(item, dict)] if isinstance(evidence, list) else []
    if not ids: return None, "EVIDENCE_ID_INVALID_FORMAT"
    for item in ids:
        if not _is_valid_evidence_id(item):
            return None, "EVIDENCE_ID_INVALID_FORMAT"
        if item not in evidence_ids:
            return None, "EVIDENCE_NOT_IN_CONTEXT" if item.startswith(("FACT-", "V2-", "TREND-", "MAXIBOT_")) else "EVIDENCE_UNKNOWN"
    for memory in value["memory_candidates"]:
        refs = memory.get("source_evidence_ids") if isinstance(memory, dict) else None
        if not isinstance(memory.get("statement") if isinstance(memory, dict) else None, str) or not refs:
            return None, "EVIDENCE_ID_INVALID_FORMAT"
        for ref in refs:
            if not _is_valid_evidence_id(ref):
                return None, "EVIDENCE_ID_INVALID_FORMAT"
            if ref not in evidence_ids:
                return None, "EVIDENCE_NOT_IN_CONTEXT" if isinstance(ref, str) and ref.startswith(("FACT-", "V2-", "TREND-", "MAXIBOT_")) else "EVIDENCE_UNKNOWN"
    return value, None

def policy(result: dict[str, Any], sufficiency: str) -> dict[str, Any]:
    action = result["recommendation"]["action_code"]
    unsafe = action in {"INCREASE_TARGET_CPA", "DECREASE_TARGET_CPA", "INCREASE_BUDGET", "DECREASE_BUDGET", "PAUSE_CAMPAIGN", "RESUME_CAMPAIGN"}
    return {"risk_level": "HIGH" if unsafe else "LOW", "displayable": sufficiency != "INSUFFICIENT" or action in {"OBSERVE", "INSUFFICIENT_DATA"}, "execution_allowed": False, "reason": "V1 read-only" if sufficiency != "INSUFFICIENT" else "insufficient_data"}
