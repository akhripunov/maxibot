import json

from research.max_cdp_runtime_instrumentation.runtime_probe import identity_verified, is_relevant, sanitize


def test_sanitize_redacts_secret_like_keys_and_bounds_strings():
    value = {"token": "secret", "nested": {"userId": "u1", "blob": "x" * 600}}
    result = sanitize(value)
    assert result["token"] == "[REDACTED]"
    assert result["nested"]["userId"] == "u1"
    assert result["nested"]["blob"].endswith("...[TRUNCATED]")


def test_relevance_is_conservative_but_accepts_join_request_markers():
    assert is_relevant({"type": "JOIN_REQUEST", "displayName": "Любимая Печенька"})
    assert not is_relevant({"displayName": "Someone else", "chatId": "channel"})


def test_probe_module_does_not_store_raw_auth_fields_in_fixture():
    text = json.dumps(sanitize({"authorization": "secret", "contact": {"id": "u1"}}))
    assert "secret" not in text


def test_identity_requires_all_fields_in_one_candidate():
    assert identity_verified({"displayName": "Любимая Печенька", "chatId": "c1", "type": "JOIN_REQUEST", "contact.id": "u1"})
    assert not identity_verified({"displayName": "Любимая Печенька", "chatId": "c1", "type": "JOIN_REQUEST"})
    assert not identity_verified({"displayName": "Любимая Печенька", "contact.id": "u1"})
