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
