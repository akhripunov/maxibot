from datetime import datetime, timezone, timedelta

from app.ads.prior_engine import (
    CONTEXT_FIELDS, aggregate_evidence, build_context_signature, promotion_scope,
    rank_priors, replay, similarity,
)
from app.ads.external_priors import validate_manifest, verify_artifact_bytes
from app.ads.prior_shadow import build_shadow_result
import hashlib


def _context(**overrides):
    value = {field: "x" for field in CONTEXT_FIELDS}
    value.update({"provider": "YANDEX_DIRECT", "destination_platform": "MAX", "promotion_object_type": "CHANNEL", **overrides})
    return value


def test_signature_is_versioned_and_deterministic():
    first = build_context_signature(_context())
    second = build_context_signature(dict(reversed(list(_context().items()))))
    assert first == second
    assert first["version"].endswith("v1") and len(first["hash"]) == 64


def test_similarity_preserves_unknowns_and_rejects_provider_mismatch():
    same = similarity(build_context_signature(_context()), build_context_signature(_context()))
    mismatch = similarity(build_context_signature(_context()), build_context_signature(_context(provider="TELEGRAM_ADS")))
    unknown = similarity(build_context_signature(_context()), build_context_signature(_context(market=None)))
    assert same.score == 1.0
    assert "provider" in mismatch.mismatched
    assert "market" in unknown.unknown


def test_evidence_dedup_technical_excluded_and_independence_counted():
    summary = aggregate_evidence([
        {"experiment_id": "1", "outcome": "SUCCESS", "context_hash": "a", "client_id": "a", "cohort_id": "c"},
        {"experiment_id": "1", "outcome": "FAIL", "context_hash": "a", "client_id": "a", "cohort_id": "c"},
        {"experiment_id": "2", "outcome": "INCONCLUSIVE", "context_hash": "b", "client_id": "b", "cohort_id": "c"},
        {"experiment_id": "tech", "experiment_kind": "TECHNICAL", "outcome": "SUCCESS"},
    ])
    assert summary["experiments"] == 2
    assert summary["independent_contexts"] == 2
    assert summary["independent_clients"] == 2


def test_scope_thresholds_never_promote_one_experiment():
    assert promotion_scope({"experiments": 1, "independent_contexts": 1, "independent_clients": 1}) == "LOCAL"
    assert promotion_scope({"experiments": 5, "independent_contexts": 3, "independent_clients": 2}) == "COHORT_CANDIDATE"
    assert promotion_scope({"experiments": 30, "independent_contexts": 10, "independent_cohorts": 3}) == "GLOBAL"


def test_rank_is_tenant_safe_external_off_and_stable():
    sig = build_context_signature(_context())
    common = {"context_signature": sig, "tenant_verified": True, "evidence": [], "observed_at": datetime.now(timezone.utc).isoformat()}
    candidates = [
        {**common, "id": "local", "tenant_id": "t1"},
        {**common, "id": "foreign", "tenant_id": "t2"},
        {**common, "id": "external", "tenant_id": None, "scope": "EXTERNAL"},
    ]
    ranked = rank_priors(candidates, sig, tenant_id="t1")
    assert [row["id"] for row in ranked] == ["local"]
    assert replay(candidates, sig, tenant_id="t1")["policy_version"]


def test_external_manifest_is_versioned_expiring_and_hash_bound():
    payload = b"offline prior artifact"
    manifest = {"version": 1, "artifact_id": "a", "schema_version": "v1", "scope": "EXTERNAL", "content_hash": hashlib.sha256(payload).hexdigest()}
    assert validate_manifest(manifest)
    assert verify_artifact_bytes(payload, manifest)
    assert not verify_artifact_bytes(b"tampered", manifest)


def test_invalid_or_expired_external_manifest_fails_closed():
    assert not validate_manifest({"version": 1, "scope": "EXTERNAL", "artifact_id": "x", "schema_version": "v1", "content_hash": "0" * 64, "expires_at": "2000-01-01T00:00:00Z"})
    assert not validate_manifest({"version": 2, "scope": "EXTERNAL", "artifact_id": "x", "schema_version": "v1", "content_hash": "0" * 64})


def test_local_evidence_is_deduped_and_fail_or_inconclusive_never_success():
    summary = aggregate_evidence([
        {"experiment_id": "a", "outcome": "FAIL", "context_hash": "c", "client_id": "t"},
        {"experiment_id": "a", "outcome": "FAIL", "context_hash": "c", "client_id": "t"},
        {"experiment_id": "b", "outcome": "INCONCLUSIVE", "context_hash": "d", "client_id": "t"},
    ])
    assert summary["experiments"] == 2 and summary["success"] == 0


def test_repeated_campaigns_do_not_fake_independence():
    summary = aggregate_evidence([{"experiment_id": str(i), "outcome": "SUCCESS", "context_hash": "same", "client_id": "same"} for i in range(8)])
    assert summary["experiments"] == 8 and summary["independent_contexts"] == 1 and summary["independent_clients"] == 1
    assert promotion_scope(summary) == "LOCAL"


def test_provider_and_market_mismatch_reduce_applicability():
    target = build_context_signature(_context(provider="YANDEX_DIRECT", market="RU"))
    other = build_context_signature(_context(provider="TELEGRAM_ADS", market="EU"))
    result = similarity(target, other)
    assert result.score < 1 and {"provider", "market"}.issubset(result.mismatched)


def test_external_prior_requires_explicit_flag_and_tenant_verification():
    sig = build_context_signature(_context())
    item = {"id": "x", "scope": "EXTERNAL", "tenant_id": None, "tenant_verified": True, "context_signature": sig, "evidence": [], "observed_at": datetime.now(timezone.utc).isoformat()}
    assert rank_priors([item], sig, tenant_id="t1") == []
    assert len(rank_priors([item], sig, tenant_id="t1", flags={"external": True})) == 1


def test_shadow_zero_priors_is_generic_and_non_authoritative():
    class Campaign:
        id = "campaign-1"
        channel_id = "channel-1"
    result = build_shadow_result(
        facts={"campaign": {"id": "campaign-1"}, "DIRECTOLOGIST": {"decision": "NO_ACTION"}},
        campaign=Campaign(), memories=[], snapshot_id="snapshot-1", tenant_id="channel-1",
    )
    assert result["cold_start_source"] == "GENERIC"
    assert result["similar_experiments"] == 0
    assert result["shadow_top_candidate"] is None
    assert result["shadow_prior_score"] == 0
    assert result["canonical_vs_shadow"] == "SHADOW_NO_CANDIDATE"


def test_shadow_comparison_is_deterministic_for_same_snapshot():
    class Campaign:
        id = "campaign-1"
        channel_id = "channel-1"
    facts = {"campaign": {"id": "campaign-1"}, "DIRECTOLOGIST": {"decision": "NO_ACTION"}}
    a = build_shadow_result(facts=facts, campaign=Campaign(), memories=[], snapshot_id="snapshot-1", tenant_id="channel-1")
    b = build_shadow_result(facts=facts, campaign=Campaign(), memories=[], snapshot_id="snapshot-1", tenant_id="channel-1")
    assert a == b
