from app.ads.llm import evidence_registry, validate_output


def _result(evidence_id):
    return {"status": "OK", "diagnosis_code": "OBSERVE", "summary": "Проверка", "confidence": "MEDIUM", "data_sufficiency": "PARTIAL", "evidence": [{"evidence_id": evidence_id, "statement": "Факт"}], "recommendation": {"action_code": "OBSERVE", "current_value": None, "suggested_value": None, "reason": "Наблюдать"}, "do_not_change": ["Direct"], "missing_data": [], "memory_candidates": []}


def test_registry_accepts_top_level_and_selected_v2_only():
    context = {"FACTS": {"evidence": {"FACT-001": {}}, "V2_ANALYSIS": {"evidence": {f"V2-{index:012x}": {} for index, _ in enumerate(("AD_GROUP", "AD", "PLACEMENT", "REGION", "DEVICE", "DEMOGRAPHICS", "CRITERIA"), 1)}}}}
    allowed = evidence_registry(context)
    assert len(allowed) == 8
    for evidence_id in allowed:
        assert validate_output(_result(evidence_id), allowed)[0] is not None


def test_database_only_and_unknown_evidence_are_rejected():
    allowed = evidence_registry({"FACTS": {"V2_ANALYSIS": {"evidence": {"V2-selected": {}}}}})
    assert validate_output(_result("V2-notselected"), allowed)[1] == "EVIDENCE_NOT_IN_CONTEXT"
    assert validate_output(_result("V2-unknown"), allowed)[1] == "EVIDENCE_NOT_IN_CONTEXT"
    assert validate_output(_result("EVIDENCE-999"), allowed)[1] == "EVIDENCE_UNKNOWN"
    assert validate_output(_result("bad id"), allowed)[1] == "EVIDENCE_ID_INVALID_FORMAT"


def test_duplicate_registry_ids_are_deterministic():
    context = {"FACTS": {"evidence": {"FACT-001": {}}, "V2_ANALYSIS": {"evidence": {"FACT-001": {}, "V2-001": {}}}}}
    assert evidence_registry(context) == {"FACT-001", "V2-001"}
