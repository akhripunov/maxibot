from datetime import date
from types import SimpleNamespace

import pytest

from app.ads.creative_experiment import (
    assert_one_creative_variable,
    creative_candidate_eligibility,
    measurement_start,
    normalized_creative_comparison,
    provider_lifecycle,
    select_existing_creative,
    tracking_params,
    variant_token,
)


def _creative(**overrides):
    values = {
        "id": "creative-a", "campaign_id": "campaign-1", "content_hash": "a" * 64,
        "creative_type": "IMAGE", "image_ref": "image-a", "external_creative_id": "10",
        "provider_state": "ACCEPTED", "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_existing_creative_selection_is_deterministic_and_scoped():
    control = _creative(id="control")
    same = _creative(id="same", content_hash=control.content_hash)
    candidate = _creative(id="candidate", content_hash="b" * 64)
    chosen, reason = select_existing_creative([same, candidate], campaign_id="campaign-1", control=control)
    assert chosen.id == "candidate"
    assert "provider-ready" in reason
    assert creative_candidate_eligibility(_creative(campaign_id="other"), campaign_id="campaign-1", control=control)[1] == "WRONG_SCOPE_CREATIVE"
    assert creative_candidate_eligibility(_creative(provider_state="REJECTED", content_hash="c" * 64), campaign_id="campaign-1", control=control)[1] == "CREATIVE_PROVIDER_REJECTED"
    assert select_existing_creative([same], campaign_id="campaign-1", control=control)[0] is None


def test_one_variable_guard_rejects_bid_text_and_targeting_changes():
    control = {"creative": "A", "bid": 10, "text": "same", "targeting": "same"}
    treatment = {"creative": "B", "bid": 11, "text": "same", "targeting": "same"}
    comparison = normalized_creative_comparison(control, treatment)
    assert comparison["allowed"] is False
    assert "bid" in comparison["differing_business_variables"]
    with pytest.raises(ValueError, match="CREATIVE_EXPERIMENT_ONLY_CREATIVE_VARIABLE_REQUIRED"):
        assert_one_creative_variable(control, treatment)
    assert_one_creative_variable({"creative": "A", "headline": "same"}, {"creative": "B", "headline": "same"})["allowed"]


def test_variant_markers_are_opaque_and_lifecycle_is_moderation_aware():
    token = variant_token("experiment-1", "CONTROL")
    assert token == tracking_params("experiment-1", "control")["maxibot_exp"]
    assert len(token) == 32 and "experiment-1" not in token
    assert provider_lifecycle({"State": "OFF", "Status": "DRAFT"}, created=True) == "SUSPENDED"
    assert provider_lifecycle({"State": "ON", "Status": "DRAFT"}, created=True) == "CREATED"
    assert provider_lifecycle({"State": "ON", "Status": "ACCEPTED"}, created=True) == "ACTIVE"
    assert provider_lifecycle({"State": "OFF", "Status": "REJECTED"}, created=True) == "REJECTED"
    assert measurement_start("CREATED") is None
    assert measurement_start("ACTIVE") is not None
