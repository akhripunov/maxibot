from __future__ import annotations

import json
import re
from copy import deepcopy
from abc import ABC, abstractmethod
from typing import Any
import httpx

from app.core.config import settings

PROMPT_VERSION = "campaign_diagnosis_v3_ru"
SCHEMA_VERSION = "campaign_diagnosis_schema_v1"
ACTION_CODES = {"HOLD", "MONITOR", "INVESTIGATE", "TEST", "SCALE_CANDIDATE", "REDUCE_CANDIDATE", "PLACEMENT_REVIEW", "ATTRIBUTION_REVIEW", "CREATIVE_REVIEW", "TARGETING_REVIEW", "OBSERVE", "INCREASE_TARGET_CPA", "DECREASE_TARGET_CPA", "REMOVE_TARGET_CPA_CONSTRAINT", "INCREASE_BUDGET", "DECREASE_BUDGET", "PAUSE_CAMPAIGN", "RESUME_CAMPAIGN", "REVIEW_GOAL", "REVIEW_ATTRIBUTION", "REVIEW_CREATIVE", "REVIEW_TARGETING", "REVIEW_PLACEMENTS", "INSUFFICIENT_DATA", "EXCLUDE_PLACEMENT", "RESTORE_PLACEMENT", "PAUSE_AD", "ENABLE_AD", "CREATE_CREATIVE_TEST", "CREATE_AD_TEST", "BOUNDED_BUDGET_REALLOCATION"}
EVIDENCE_ID_PATTERN = re.compile(r"(?:FACT-\d{3}|V2-[A-Za-z0-9]+|EVIDENCE-\d+|TREND-[A-Za-z0-9]+|MAXIBOT_QUALIFIED_MEMBERSHIP_RATE|MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS)")
SECRET_MARKERS = ("access_token", "refresh_token", "authorization", "api_key", "cookie", "password", "secret", "oauth")

SYSTEM_PROMPT = """Use only supplied context, especially the bounded V2_ANALYSIS dataset and DIRECTOLOGIST deterministic decision pack. FACTS.TRENDS contains deterministic Trend Intelligence V1 features computed from non-overlapping completed daily buckets; explain those features, do not recalculate trends from raw rows. UNKNOWN is not zero. Do not invent metrics or causality. Campaign totals are the source of truth; dimensions explain structure and must never be summed together. Use only findings with sufficient sample and HIGH materiality for strong wording; WEAK_SIGNAL means 'есть сигнал', INSUFFICIENT means no conclusion. DIRECTOLOGIST confirmed outcomes are the MaxiBot business truth: CHANNEL uses confirmed max_subscriber/TrafficMembership and BOT uses confirmed bot_started/BotConversion. Yandex conversions are a separate provider signal. Attribution quality labels OBSERVED, MATCHED, MODELED, UNRESOLVED, and UNATTRIBUTED must remain distinct. FACTS.MAXIBOT_QUALIFIED_MEMBERSHIP_RATE is a visit-to-qualified-membership conversion: numerator qualified_memberships, denominator tracked_landing_visits. Never call it attribution coverage, attribution quality, Yandex conversion confirmation, campaign attribution rate, or matching coverage. CANONICAL ATTRIBUTION PRECEDENCE: FACTS.campaign_specific_attribution (same as FACTS.MAXIBOT_METRICS.campaign_specific) is the authoritative UTM-based TrafficMembership-to-campaign result. If supported=true and value is positive, campaign-specific membership attribution is available and must never be described as unavailable, unknown, unsupported, or absent. A small sample may limit optimization confidence or prevent a confident CPA conclusion, but it does not erase supported attribution. Null campaign_specific fields inside the per-window 1d/3d/7d/30d objects mean those aggregates are CHANNEL-wide and not campaign-scoped; they do not override the canonical campaign_specific_attribution object. DIRECTOLOGIST.outcomes uses its own yclid/identity-linked outcome path; zero outcomes or null coverage there are scoped to that separate path and do not override UTM-matched campaign_specific_attribution. Keep those two attribution paths distinct and explain the scope if both are mentioned. Keep campaign-specific matching separate; only when the canonical campaign_specific_attribution is supported=false (or campaign_specific_matching is UNKNOWN and no supported canonical field exists), say that matching with a specific campaign is unavailable. Separate confidence_axes.performance_confidence (Yandex-reported performance) from confidence_axes.attribution_confidence (confidence in campaign-specific matching only). Low campaign-specific matching confidence must not invalidate healthy Yandex results. Keep Yandex conversions and MaxiBot memberships as separate populations. Within MaxiBot memberships, keep campaign-specific and channel-level counts as separate populations; never equate any of these three populations. PREVIOUS_RECOMMENDATIONS and MEASURED_OUTCOMES are bounded historical context: distinguish suggested actions marked RECOMMENDATION_ONLY from executed actions, and distinguish pending or not-available outcomes from completed measurements. Observed after-metrics do not prove causality. Use the deterministic recommendation_quality fields as advisory ranking context: action_ladder, signal_evolution, WHY_THIS, WHY_NOW, EVIDENCE, RISK_IF_IGNORED, NEXT_STEP, and WAIT_CONDITION; do not create extra recommendations from duplicate current, trend, or dimension signals. Rank one primary action. You may propose one V1 action code, but never propose execution, never claim it has been executed, and never invent target identifiers; execution is decided exclusively by the deterministic Control Plane. Cite only FACT or V2 evidence IDs present in context. Return JSON only matching campaign_diagnosis_schema_v1. Write all human-readable fields in Russian: summary, evidence.statement, recommendation.reason, do_not_change, and memory_candidates.statement. Keep diagnosis_code and recommendation.action_code as stable English codes."""

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
    ids.update((facts.get("DIRECTOLOGIST") or {}).get("evidence", {}).keys())
    return {item for item in ids if isinstance(item, str)}


def _is_valid_evidence_id(value: Any) -> bool:
    return isinstance(value, str) and bool(EVIDENCE_ID_PATTERN.fullmatch(value))

_CAMPAIGN_ATTRIBUTION_NEGATION_PATTERNS = (
    re.compile(r"campaign[\s_-]*specific.{0,60}(?:unavailable|not available|unknown|unsupported|not supported|absent|not confirmed|not proven|missing)", re.I),
    re.compile(r"campaign_specific\s*=\s*false", re.I),
    re.compile(r"no\s+(?:confirmed\s+)?campaign[\s_-]*specific\s+(?:attribution|matching|memberships?|evidence|outcomes?)", re.I),
    re.compile(r"нет\s+(?:подтвержд[её]нн\w*\s+)?campaign[\s_-]*specific\s+(?:attribution|matching|memberships?|evidence|outcomes?)", re.I),
    re.compile(r"(?:сопоставлени[ея]|атрибуци[яию])\s+(?:подписок\s+)?с\s+конкретн\w+\s+кампан\w+.{0,70}(?:недоступн|не подтвержд[её]н|отсутств|неизвестн)", re.I),
    re.compile(r"(?:кампанийн\w*\s+(?:атрибуци\w*|соответстви\w*|сопоставлени\w*)|campaign[\s_-]*specific\s+(?:attribution|matching)).{0,70}(?:недоступн|не подтвержд[её]н|отсутств|неизвестн|не подтверждено)", re.I),
    re.compile(r"(?:нет|отсутству\w*)\s+(?:доказательств|подтвержд[её]нн\w*\s+(?:подписок|memberships?)|подтвержд[её]нн\w*\s+исходов).{0,70}(?:кампан\w+|campaign)", re.I),
    re.compile(r"(?:кампанийн\w*\s+(?:атрибуци\w*|соответстви\w*|сопоставлени\w*)).{0,70}(?:недоступн|не подтвержд[её]н|отсутств|неизвестн|не подтверждено)", re.I),
    re.compile(r"(?:недостаточн\w*|нет)\s+(?:над[её]жн\w*\s+)?подтверждени\w*\s+кампанийн\w*\s+(?:соответстви\w*|сопоставлени\w*)", re.I),
    re.compile(r"над[её]жн\w*\s+подтверждени\w*\s+кампанийн\w*\s+(?:соответстви\w*|сопоставлени\w*)", re.I),
)


def _canonical_campaign_mapping(context: dict[str, Any] | None) -> dict[str, Any] | None:
    facts = (context or {}).get("FACTS") or {}
    mapping = facts.get("campaign_specific_attribution")
    if not isinstance(mapping, dict):
        mapping = (facts.get("MAXIBOT_METRICS") or {}).get("campaign_specific")
    return mapping if isinstance(mapping, dict) else None


def _scope_directologist_evidence(value: dict[str, Any], context: dict[str, Any]) -> None:
    """Make the distinct yclid/identity outcome path explicit in its evidence text."""
    for row in value.get("evidence", []) if isinstance(value.get("evidence"), list) else []:
        if not isinstance(row, dict) or row.get("evidence_id") != "V2-DIRECTOLOGISTOUTCOMES":
            continue
        facts = context.get("FACTS") or {}
        directologist = facts.get("DIRECTOLOGIST") or {}
        outcomes = directologist.get("outcomes") or {}
        attributed = outcomes.get("attributed_outcomes")
        coverage = outcomes.get("attribution_coverage")
        row["statement"] = (
            "Отдельный путь DIRECTOLOGIST через yclid/связь identity: "
            f"кампанийных исходов={attributed if attributed is not None else 'UNKNOWN'}, "
            f"покрытие={'UNKNOWN' if coverage is None else coverage}. "
            "Этот результат относится только к yclid/identity-сопоставлению и не отменяет "
            "отдельную UTM-атрибуцию TrafficMembership из canonical campaign-specific fact."
        )


def _has_campaign_attribution_contradiction(value: dict[str, Any]) -> bool:
    def walk(item: Any) -> bool:
        if isinstance(item, dict):
            return any(walk(child) for child in item.values())
        if isinstance(item, list):
            return any(walk(child) for child in item)
        if isinstance(item, str):
            return any(pattern.search(item) for pattern in _CAMPAIGN_ATTRIBUTION_NEGATION_PATTERNS)
        return False
    return walk(value)


def validate_output(value: Any, evidence_ids: set[str], context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
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
    result = deepcopy(value)
    mapping = _canonical_campaign_mapping(context)
    try:
        mapped_value = float(mapping.get("value")) if mapping and mapping.get("value") is not None else 0
    except (TypeError, ValueError):
        mapped_value = 0
    if mapping and mapping.get("supported") is True and mapped_value > 0 and context:
        _scope_directologist_evidence(result, context)
        if _has_campaign_attribution_contradiction(result):
            return None, "CAMPAIGN_ATTRIBUTION_CONTRADICTION"
    return result, None

def policy(result: dict[str, Any], sufficiency: str) -> dict[str, Any]:
    action = result["recommendation"]["action_code"]
    unsafe = action in {"INCREASE_TARGET_CPA", "DECREASE_TARGET_CPA", "INCREASE_BUDGET", "DECREASE_BUDGET", "PAUSE_CAMPAIGN", "RESUME_CAMPAIGN"}
    return {"risk_level": "HIGH" if unsafe else "LOW", "displayable": sufficiency != "INSUFFICIENT" or action in {"OBSERVE", "INSUFFICIENT_DATA"}, "execution_allowed": False, "reason": "V1 read-only" if sufficiency != "INSUFFICIENT" else "insufficient_data"}
