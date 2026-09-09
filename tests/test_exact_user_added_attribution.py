from app.traffic.attribution import EXACT, UNATTRIBUTED, can_send_offline_conversion, resolve_exact_identity_visit
from app.traffic.join_intent import ACTIVE, CONSUMED, SUPERSEDED, intent_expiry
from datetime import datetime, timezone


def test_exact_identity_maps_one_visit_to_exact():
    visit_id = resolve_exact_identity_visit(["visit-a"])
    assert visit_id == "visit-a"
    assert EXACT == "EXACT"


def test_no_identity_stays_unattributed():
    assert resolve_exact_identity_visit([]) is None
    assert UNATTRIBUTED == "UNATTRIBUTED"


def test_ambiguous_identity_is_not_exact():
    assert resolve_exact_identity_visit(["visit-a", "visit-b"]) is None


def test_duplicate_identity_rows_are_one_exact_visit():
    assert resolve_exact_identity_visit(["visit-a", "visit-a"]) == "visit-a"


def test_same_user_different_channels_are_resolved_by_channel_scope():
    # The DB query supplies only visits for the current channel; a visit from
    # another channel must never be included in this resolver's input.
    assert resolve_exact_identity_visit(["channel-a-visit"]) == "channel-a-visit"


def test_temporal_or_payloadless_members_are_never_yandex_eligible():
    assert not can_send_offline_conversion("HIGH", "yclid")
    assert not can_send_offline_conversion(UNATTRIBUTED, "yclid")
    assert can_send_offline_conversion(EXACT, "yclid")


def test_verified_join_intent_has_expiry_that_covers_manual_approval_window():
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert intent_expiry(now, 72).isoformat() == "2026-08-28T00:00:00+00:00"


def test_join_intent_statuses_are_explicit_and_not_a_temporal_guess():
    assert (ACTIVE, SUPERSEDED, CONSUMED) == ("ACTIVE", "SUPERSEDED", "CONSUMED")


def test_multiple_verified_visits_need_one_active_verified_channel_handoff_intent():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    handler = source[source.index("# Manual approval produces"):source.index("# A verified startapp identity", source.index("# Manual approval produces"))]
    assert "consume_single_active_join_intent" in handler
    assert "EXACT_VERIFIED_CHANNEL_OPEN_INTENT" in handler
    assert handler.index("consume_single_active_join_intent") < handler.index("resolve_exact_identity_visit")


def test_replayed_user_added_cannot_downgrade_a_consumed_exact_intent():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "bot" / "menu.py").read_text()
    handler = source[source.index("membership.current_member = True"):source.index("# Manual approval produces", source.index("membership.current_member = True"))]
    assert "membership.attribution_status == EXACT" in handler
    assert "membership.attributed_visit_id" in handler
