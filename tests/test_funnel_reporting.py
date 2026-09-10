from app.traffic.funnel_reporting import FunnelObservation, canonical_paid_click_report, campaign_matches


def test_campaign_matches_real_yandex_utm_component_format():
    assert campaign_matches("msk365_rsya_msk_click_test1.713798021.5791778274", "713798021")
    assert not campaign_matches("msk365_rsya_msk_click_test1.713798021.5791778274", "713798022")


def test_report_distinguishes_unique_yclid_from_raw_requests_and_revisits():
    report = canonical_paid_click_report([
        FunnelObservation("a", "m365", frozenset({"MAX_LINK_ASSIGNED"})),
        FunnelObservation("a", "m365", frozenset({"MINI_APP_LOADED", "max_identity_linked"})),
        FunnelObservation("b", "m365.713", frozenset({"MINIAPP_CHANNEL_HANDOFF_STARTED"})),
        FunnelObservation(None, "m365", frozenset()),
        FunnelObservation("external", "other", frozenset()),
    ], requested_campaign="m365")

    assert report["raw_landing_requests"] == 5
    assert report["campaign_matched_raw_requests"] == 4
    assert report["direct_attributed_raw_yclid_requests"] == 3
    assert report["direct_attributed_unique_yclid"] == 2
    assert report["duplicate_or_revisit_requests"] == 1
    assert report["campaign_matched_missing_yclid_requests"] == 1
    assert report["other_or_unmatched_requests"] == 1
    assert report["deepest_stage_by_unique_yclid"]["IDENTITY_BOUND"] == 1
    assert report["deepest_stage_by_unique_yclid"]["CHANNEL_HANDOFF_STARTED"] == 1


def test_report_does_not_need_or_return_identifiers():
    report = canonical_paid_click_report([FunnelObservation("secret", "m365", frozenset())], requested_campaign="m365")
    assert "secret" not in str(report)
