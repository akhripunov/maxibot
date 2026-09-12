from app.ads.creative_builder import build_creative_candidate


def test_candidate_is_complete_and_content_addressed_without_owner_payload():
    candidate = build_creative_candidate(
        campaign_id="campaign-1", experiment_id="exp-1", objective="userDidSubscribe",
        target_cpa=20, hypothesis="Проверить новый креатив",
    )
    assert candidate["headline"] and candidate["text"] and candidate["cta"]
    assert candidate["image_bytes"].startswith(b"\x89PNG")
    assert len(candidate["content_hash"]) == 64
    assert candidate["treatment_specification"]["objective"] == "userDidSubscribe"
