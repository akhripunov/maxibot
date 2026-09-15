from datetime import datetime, timedelta, timezone

from app.ads.periodic import analysis_due


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
