from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ads.codex_worker import COMPLETED, FAILED, PROCESSING, WAITING, lease_active, transition, worker_enabled
from app.core.config import settings


ROOT = Path(__file__).resolve().parents[1]


def test_worker_is_opt_in_and_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ads_enabled", True)
    monkeypatch.setattr(settings, "ads_codex_worker_enabled", False)
    monkeypatch.setattr(settings, "ads_llm_provider", "codex_worker")
    assert worker_enabled() is False
    monkeypatch.setattr(settings, "ads_codex_worker_enabled", True)
    assert worker_enabled() is True


def test_state_machine_and_stale_lease_fail_closed():
    run = SimpleNamespace(status=WAITING)
    transition(run, PROCESSING)
    assert run.status == PROCESSING
    with pytest.raises(ValueError):
        transition(run, PROCESSING)
    transition(run, COMPLETED)
    with pytest.raises(ValueError):
        transition(run, FAILED)
    active = SimpleNamespace(status=PROCESSING, lease_id="lease-123", lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert lease_active(active, "lease-123")
    assert not lease_active(active, "wrong-lease")


def test_worker_contract_has_atomic_claim_and_retry_controls():
    source = (ROOT / "app" / "ads" / "codex_worker.py").read_text()
    assert "skip_locked=True" in source
    assert "LEASE_RETRIES_EXHAUSTED" in source
    assert "retry_count+=1" in source


def test_codex_exec_uses_neutral_temp_cwd_and_shared_schema():
    script = (ROOT / "scripts" / "ads_codex_worker.py").read_text()
    schema = ROOT / "schemas" / "campaign_diagnosis_schema_v1.json"
    assert schema.exists()
    assert '"--ephemeral"' in script
    assert '"--skip-git-repo-check"' in script
    assert 'cwd=directory' in script
    assert 'schema_source' in script
    assert "TOKEN" in script and "print" not in script


def test_codex_prompt_lists_only_exact_evidence_ids_from_safe_context():
    from scripts.ads_codex_worker import allowed_evidence_ids, prompt
    from app.ads.llm import evidence_registry

    safe_context = {
        "FACTS": {
            "evidence": {
                "FACT-001": {}, "FACT-002": {},
                "MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS": {},
            },
            "campaign_specific_attribution": {"supported": True},
            "V2_ANALYSIS": {
                "evidence": {"V2-0123456789ab": {}},
                "trends": {"findings": [{"trend_finding_id": "TREND-acde01234567"}]},
            },
            "DIRECTOLOGIST": {"evidence": {"V2-DIRECTOLOGISTCURRENT": {}}},
        }
    }
    claim = {"safe_context": safe_context}

    assert allowed_evidence_ids(safe_context) == [
        "FACT-001", "FACT-002", "MAXIBOT_CAMPAIGN_SPECIFIC_MEMBERSHIPS",
        "TREND-acde01234567", "V2-0123456789ab", "V2-DIRECTOLOGISTCURRENT",
    ]
    assert set(allowed_evidence_ids(safe_context)) == evidence_registry({"FACTS": safe_context["FACTS"]})
    prompt_text = prompt(claim)
    assert "Разрешённые evidence IDs (копируй только их, без хеширования и генерации):" in prompt_text
    assert '"V2-0123456789ab"' in prompt_text
    assert "V2-202373d7b1ab" not in prompt_text


def test_production_polling_and_direct_writes_are_not_enabled_by_worker():
    script = (ROOT / "scripts" / "ads_codex_worker.py").read_text()
    assert "Yandex" not in script
    assert "Campaigns.update" not in script


def test_ai_dashboard_is_read_only_and_status_refresh_does_not_start_a_run():
    menu = (ROOT / "app" / "bot" / "menu.py").read_text()
    runtime = (ROOT / "app" / "bot" / "runtime.py").read_text()
    assert '"🤖 AI Продвижение", f"ads:{channel.id}"' in menu
    assert 'f"ads:{selected_id}"' in runtime
    assert '"ads_status:"' in runtime
    assert '"🔒 Теневой режим' in menu
    assert "Apply" not in menu
    assert "Применить" not in menu
