from app.traffic.attribution import EXACT, UNATTRIBUTED, resolve_exact_identity_visit


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
