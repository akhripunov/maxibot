from pathlib import Path


def test_worker_has_stage_specific_safe_diagnostics():
    source = (Path(__file__).parents[1] / "scripts" / "ads_codex_worker.py").read_text()
    for code in (
        "CODEX_BINARY_NOT_FOUND", "CODEX_AUTH_FAILED", "CODEX_EXEC_TIMEOUT",
        "CODEX_EXEC_NONZERO", "CODEX_EMPTY_OUTPUT", "CODEX_JSON_PARSE_FAILED",
        "CODEX_SCHEMA_VALIDATION_FAILED", "CODEX_EVIDENCE_VALIDATION_FAILED",
        "CODEX_RESULT_UPLOAD_FAILED", "CODEX_UNKNOWN_RUNTIME_ERROR",
    ):
        assert code in source
    assert "stderr_tail" in source
    assert "worker_diagnostics_json" in (Path(__file__).parents[1] / "app" / "db" / "models.py").read_text()
