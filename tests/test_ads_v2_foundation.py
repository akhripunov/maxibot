from datetime import date, datetime, timezone

from app.ads.facts import build_campaign_facts


def test_v2_window_provenance_is_per_fact_and_uses_moscow_calendar():
    facts = build_campaign_facts(
        [{"Date": "2026-09-03", "Impressions": "100", "Clicks": "15", "Cost": "0", "Conversions": "0"}],
        campaign_start="2026-09-01",
        traffic={},
        now=datetime(2026, 9, 3, 6, 40, tzinfo=timezone.utc),
    )
    evidence = facts.evidence(
        report_date_from=date(2026, 8, 28),
        report_date_to=date(2026, 9, 3),
        report_fetched_at=datetime(2026, 9, 3, 6, 40, tzinfo=timezone.utc),
        snapshot_at=datetime(2026, 9, 3, 6, 40, tzinfo=timezone.utc),
    )
    assert evidence["FACT-005"]["provenance"]["window"] == "1d"
    assert evidence["FACT-005"]["provenance"]["date_from"] == "2026-09-03"
    assert evidence["FACT-005"]["provenance"]["date_to"] == "2026-09-03"
    assert evidence["FACT-005"]["provenance"]["timezone"] == "Europe/Moscow"
    assert evidence["FACT-008"]["provenance"]["raw_value"] == 0.0
