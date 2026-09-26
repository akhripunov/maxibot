from app.ads.llm import SYSTEM_PROMPT, validate_output
from app.ads.attribution import campaign_attribution_confidence


def _decision(summary: str, *, evidence_statement: str | None = None) -> dict:
    return {
        "status": "OK",
        "diagnosis_code": "MONITOR",
        "summary": summary,
        "confidence": "MEDIUM",
        "data_sufficiency": "PARTIAL",
        "evidence": [{
            "evidence_id": "MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS",
            "statement": evidence_statement or "UTM-атрибуция кампании доступна: 4 подтверждённых подписки.",
        }],
        "recommendation": {
            "action_code": "MONITOR",
            "current_value": "4 кампанийные подписки MaxiBot; Direct conversions — отдельная метрика",
            "suggested_value": None,
            "reason": "Продолжить наблюдение: выборка пока небольшая.",
        },
        "do_not_change": [],
        "missing_data": [],
        "memory_candidates": [],
    }


def _context(*, supported: bool = True, value: int | None = 4) -> dict:
    return {
        "FACTS": {
            "campaign_specific_attribution": {
                "supported": supported,
                "value": value,
                "campaign_id": "714373352",
                "metric": "MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS",
            },
            "MAXIBOT_METRICS": {
                "campaign_specific": {"supported": supported, "value": value},
                "7d": {"qualified_memberships": 11, "campaign_specific": None, "scope": "CHANNEL"},
            },
            "facts": {"conversions_7d": 3},
            "DIRECTOLOGIST": {
                "outcomes": {"attributed_outcomes": 0, "attribution_coverage": None},
            },
        },
        "RECENT_HISTORY": [{"claim": "old UNKNOWN: campaign-specific attribution unavailable"}],
    }


def test_supported_campaign_mapping_must_be_acknowledged_in_explanation():
    result, error = validate_output(
        _decision("Campaign-specific UTM attribution is available and confirms 4 memberships; the small sample limits optimization confidence."),
        {"MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS"},
        _context(),
    )
    assert error is None
    assert result["summary"].endswith("confidence.")


def test_unknown_mapping_remains_unknown_and_is_not_rejected():
    result, error = validate_output(
        _decision("Сопоставление с конкретной кампанией неизвестно."),
        {"MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS"},
        _context(supported=False, value=None),
    )
    assert error is None
    assert result is not None


def test_channel_and_campaign_memberships_and_direct_conversions_are_separate():
    result, error = validate_output(
        _decision("Direct: 3 conversions; MaxiBot: 11 подписок на уровне канала, из них 4 сопоставлены с кампанией через UTM. Это разные группы событий."),
        {"MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS"},
        _context(),
    )
    assert error is None
    assert result is not None
    assert "Keep Yandex conversions and MaxiBot memberships as separate populations" in SYSTEM_PROMPT
    assert "Within MaxiBot memberships, keep campaign-specific and channel-level counts as separate populations" in SYSTEM_PROMPT
    assert "Null campaign_specific fields inside the per-window" in SYSTEM_PROMPT


def test_prior_unknown_does_not_override_fresh_supported_canonical_fact():
    context = _context()
    context["PREVIOUS_RECOMMENDATIONS"] = [{"summary": "campaign-specific attribution unavailable"}]
    result, error = validate_output(
        _decision("UTM campaign-specific attribution is supported with 4 confirmed memberships."),
        {"MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS"},
        context,
    )
    assert error is None
    assert result is not None


def test_attribution_confidence_uses_campaign_match_coverage_not_channel_membership_rate():
    # The channel conversion rate is 11/166 (~6.6%), but the campaign mapping
    # coverage is 4/11; the attribution axis must reflect the latter.
    assert campaign_attribution_confidence({"supported": True, "value": 4, "coverage": 4 / 11}) == "MEDIUM"
    assert campaign_attribution_confidence({"supported": False, "value": None, "coverage": None}) == "INSUFFICIENT"


def test_codex_contradiction_fails_validation_before_persistence():
    for claim in (
        "Сопоставление подписок с конкретной кампанией недоступно.",
        "No campaign-specific evidence is available.",
        "Нет подтверждённых campaign-specific memberships.",
        "campaign-specific attribution недоступна.",
        "campaign_specific=false для подписок.",
    ):
        result, error = validate_output(
            _decision(claim),
            {"MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS"},
            _context(),
        )
        assert result is None, claim
        assert error == "CAMPAIGN_ATTRIBUTION_CONTRADICTION", claim


def test_directologist_zero_is_scoped_to_its_separate_yclid_path():
    output = _decision("Доступная UTM-атрибуция показывает 4 membership.")
    output["evidence"].append({
        "evidence_id": "V2-DIRECTOLOGISTOUTCOMES",
        "statement": "В сводке исходов 0; campaign-specific attribution coverage недоступно (null).",
    })
    result, error = validate_output(
        output,
        {"MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS", "V2-DIRECTOLOGISTOUTCOMES"},
        _context(),
    )
    assert error is None
    statement = result["evidence"][1]["statement"]
    assert "Отдельный путь DIRECTOLOGIST через yclid" in statement
    assert "не отменяет" in statement
