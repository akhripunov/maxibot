def test_primary_poll_marker_is_durable_and_advances_after_handling():
    from pathlib import Path

    root = Path(__file__).parents[1]
    source = (root / "app" / "bot" / "runtime.py").read_text()
    assert "self.marker = await self._load_primary_marker()" in source
    assert "await self._store_primary_marker(next_marker)" in source
    assert 'db.get(MaxPollingCheckpoint, "primary")' in source


def test_membership_reconciliation_is_limited_to_verified_active_handoffs():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "traffic" / "membership_reconciliation.py").read_text()
    assert "TrafficJoinIntent.status == ACTIVE" in source
    assert "TrafficJoinIntent.expires_at >= now" in source
    assert "membership_checked_at.asc().nullsfirst()" in source
    assert "await client.get_member" in source
    assert 'metadata={"source": "official_member_lookup"}' in source
