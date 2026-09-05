import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.db.models import QaRun, QaSuiteResult
from app.qa_observability import normalize_qa_result, parity_status, persist_qa_result


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def artifact(**overrides):
    value = {
        "schema_version": "qa_observability.v1",
        "release_id": "2026-09-05T100000Z",
        "tested_sha": SHA,
        "current_main_sha": SHA,
        "production_sha": "UNKNOWN",
        "verdict": "GO",
        "timing": {"started_at": "2026-09-05T10:00:00Z", "finished_at": "2026-09-05T10:01:00Z", "duration_ms": 60000},
        "counts": {"total_tests": 10, "passed": 10, "failed": 0, "skipped": 0},
        "production": {"status": "WAITING_OWNER_APPROVAL", "smoke": "NOT_RUN", "changed": False},
        "suites": [{"name": "unit", "status": "PASS", "total_tests": 10, "passed": 10, "failed": 0, "skipped": 0, "duration_ms": 100}],
        "failures": [{"name": "safe.test", "status": "FAIL", "message": "secret-token-and-cookie-must-not-persist"}],
        "case_registry": {"unknown": 2, "unknown_critical": 1},
    }
    value.update(overrides)
    return value


def test_normalize_requires_exact_tested_sha_and_sanitizes_failures():
    result = normalize_qa_result(artifact())
    assert result["tested_sha"] == SHA
    assert result["failures"] == [{"name": "safe.test", "status": "FAIL"}]
    assert result["unverified"] == {"total": 2, "critical": 1, "statuses": ["WORKING_UNTESTED", "UNKNOWN"]}
    with pytest.raises(ValueError):
        normalize_qa_result(artifact(tested_sha="not-a-commit"))


def test_parity_is_explicitly_unknown_without_comparable_sha():
    assert parity_status(SHA, None) == "UNKNOWN"
    assert parity_status(SHA, "b" * 40) == "MISMATCH"
    assert parity_status(SHA, SHA) == "MATCH"


@pytest.mark.asyncio
async def test_qa_run_and_suite_persistence_is_idempotent():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(QaRun.__table__.create)
        await connection.run_sync(QaSuiteResult.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        first = await persist_qa_result(db, artifact())
        second = await persist_qa_result(db, artifact(suites=[{"name": "regression", "status": "NOT_RUN"}]))
        assert first.id == second.id
        assert second.tested_sha == SHA
        assert second.unverified_json == json.dumps({"total": 2, "critical": 1, "statuses": ["WORKING_UNTESTED", "UNKNOWN"]}, ensure_ascii=False)
        suites = list((await db.scalars(select(QaSuiteResult).where(QaSuiteResult.run_id == second.id))).all())
        assert {suite.suite_name for suite in suites} == {"static", "unit", "integration", "regression", "build", "staging", "e2e"}
    await engine.dispose()


def test_qa_runner_emits_machine_artifact_and_no_raw_failure_messages():
    script = (ROOT / "scripts/qa_check.sh").read_text(encoding="utf-8")
    assert '"schema_version": "qa_observability.v1"' in script
    assert '"tested_sha": os.environ["CANDIDATE_SHA"]' in script
    assert '"artifact_sha256"' in script
    assert '"failures": []' in script
    assert "qa-result.json" in script


def test_web_admin_qa_routes_are_owner_read_only_and_ingest_is_edge_only():
    source = (ROOT / "app/web_admin_readonly.py").read_text(encoding="utf-8")
    assert '@router.get("/qa/current")' in source
    assert '@router.get("/qa/runs")' in source
    assert '@router.get("/qa/runs/{run_id}")' in source
    assert '@router.post("/qa-runs/ingest", dependencies=[Depends(require_edge_token)])' in source
    assert 'Depends(require_web_admin)' in source
