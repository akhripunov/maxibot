from datetime import datetime, timedelta, timezone

from app.ads.periodic import analysis_due
from app.ads.codex_worker import FAILED, PROCESSING, WAITING, expire_stale_pending_run
from app.core.config import settings
from types import SimpleNamespace


def test_continuous_analysis_is_bounded_by_cadence():
    now = datetime.now(timezone.utc)
    assert analysis_due(last_analysis_at=None, latest_data_at=None, latest_outcome_at=None, objective_updated_at=None, now=now, cadence_minutes=360)
    recent = now - timedelta(minutes=10)
    assert not analysis_due(last_analysis_at=recent, latest_data_at=now, latest_outcome_at=None, objective_updated_at=None, now=now, cadence_minutes=360)
    old = now - timedelta(minutes=361)
    assert analysis_due(last_analysis_at=old, latest_data_at=now, latest_outcome_at=None, objective_updated_at=None, now=now, cadence_minutes=360)


def test_conclusive_outcome_triggers_next_cycle_without_waiting_for_cadence():
    now = datetime.now(timezone.utc)
    assert analysis_due(last_analysis_at=now - timedelta(minutes=1), latest_data_at=None, latest_outcome_at=now, objective_updated_at=None, now=now, cadence_minutes=360)


def test_objective_change_triggers_next_cycle_but_unchanged_data_does_not():
    now = datetime.now(timezone.utc)
    last = now - timedelta(minutes=10)
    assert analysis_due(last_analysis_at=last, latest_data_at=last, latest_outcome_at=None, objective_updated_at=now, now=now, cadence_minutes=360)
    assert not analysis_due(last_analysis_at=last, latest_data_at=last, latest_outcome_at=None, objective_updated_at=None, now=now, cadence_minutes=360)


def test_continuous_mode_is_wired_to_existing_periodic_collector_and_is_configurable():
    source = open("app/ads/periodic.py", encoding="utf-8").read()
    config = open("app/core/config.py", encoding="utf-8").read()
    assert "await trigger_continuous_analysis(session_factory)" in source
    assert "ads_analysis_cadence_minutes" in config
    assert "ads_background_analysis_enabled" in source


def test_periodic_analysis_preserves_snapshot_provenance_and_collection_link():
    source = open("app/ads/periodic.py", encoding="utf-8").read()
    service = open("app/ads/service.py", encoding="utf-8").read()
    compose = open("docker-compose.vps.yml", encoding="utf-8").read()
    assert 'snapshot_source="PERIODIC_ANALYSIS"' in source
    assert "latest_collection.snapshot_id = snapshot_id" in source
    assert 'source=snapshot_source' in service
    assert 'ADS_BACKGROUND_ANALYSIS_ENABLED: "true"' in compose


def test_orphaned_waiting_codex_run_expires_without_erasing_its_audit_record(monkeypatch):
    now = datetime(2026, 9, 26, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(settings, "ads_codex_run_wait_timeout_minutes", 360)
    stale = SimpleNamespace(
        status=WAITING,
        created_at=now - timedelta(days=9),
        completed_at=None,
        validation_error=None,
        worker_id=None,
        lease_id=None,
        lease_expires_at=None,
    )

    assert expire_stale_pending_run(stale, now=now)
    assert stale.status == FAILED
    assert stale.validation_error == "CODEX_RUN_TIMEOUT"
    assert stale.completed_at == now


def test_codex_pending_timeout_honors_recent_runs_and_live_worker_leases(monkeypatch):
    now = datetime(2026, 9, 26, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(settings, "ads_codex_run_wait_timeout_minutes", 360)
    recent_waiting = SimpleNamespace(status=WAITING, created_at=now - timedelta(minutes=30), lease_expires_at=None)
    leased_processing = SimpleNamespace(status=PROCESSING, created_at=now - timedelta(days=2), lease_expires_at=now + timedelta(minutes=1))

    assert not expire_stale_pending_run(recent_waiting, now=now)
    assert not expire_stale_pending_run(leased_processing, now=now)
