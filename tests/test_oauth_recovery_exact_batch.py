from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "scripts/retry_oauth_recovery_exact_batch.py").read_text()


def test_oauth_recovery_retry_is_exact_and_count_guarded():
    assert 'expected = int(os.environ["EXPECTED_PENDING_CONVERSIONS"])' in SOURCE
    assert 'OfflineConversion.last_error == "KeyError"' in SOURCE
    assert 'TrafficMembership.attribution_status == "EXACT"' in SOURCE
    assert 'if len(rows) != expected:' in SOURCE
    assert "await _send_exact_conversion(db, conversion, membership, channel)" in SOURCE
