from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import json
from cryptography.fernet import Fernet

from app.telegram_executor.identity_store import IdentityStore, IdentityStoreError, export_sealed_bundle, import_sealed_bundle
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from app.telegram_executor.protocol import ExecutionOperation, TelegramExecutionRequestV1
from app.telegram_executor.security import assert_telegram_url


def store(tmp_path: Path) -> IdentityStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    key = tmp_path / "master.key"
    key.write_bytes(Fernet.generate_key())
    return IdentityStore(tmp_path / "identities", key)


def test_identity_encrypted_roundtrip_and_owner_allowlist(tmp_path):
    s = store(tmp_path)
    ref = uuid4()
    s.put(ref, {"session": "opaque"}, allowed_owner_identifiers=["org-a"])
    assert s.status(ref, "org-a")["owner_allowed"] is True
    assert s.status(ref, "org-b")["owner_allowed"] is False
    assert (tmp_path / "identities" / f"{ref}.identity").read_text() != '{"session":"opaque"}'


def test_identity_wrong_key_fails_closed(tmp_path):
    s = store(tmp_path)
    ref = uuid4()
    s.put(ref, {"cookie": "secret"}, allowed_owner_identifiers=["org-a"])
    other = tmp_path / "other.key"
    other.write_bytes(Fernet.generate_key())
    with pytest.raises(IdentityStoreError):
        IdentityStore(tmp_path / "identities", other).get(ref)


def test_sealed_bundle_roundtrip(tmp_path):
    source = store(tmp_path / "source")
    target = store(tmp_path / "target")
    ref = uuid4()
    source.put(ref, {"cookie": "opaque" * 100}, allowed_owner_identifiers=["org-a"])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    bundle = export_sealed_bundle(source, ref, public, expiry=datetime.now(timezone.utc) + timedelta(minutes=5), allowed_owner_ids=["org-a"])
    assert import_sealed_bundle(target, bundle, private) == ref
    assert target.status(ref, "org-a")["owner_allowed"] is True
    with pytest.raises(IdentityStoreError, match="IDENTITY_ALREADY_EXISTS"):
        import_sealed_bundle(target, bundle, private)


def test_sealed_bundle_negative_matrix(tmp_path):
    source = store(tmp_path / "source")
    ref = uuid4()
    source.put(ref, {"cookie": "opaque"}, allowed_owner_identifiers=["org-a"])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    other_private = other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    expired = export_sealed_bundle(source, ref, public, expiry=datetime.now(timezone.utc) - timedelta(minutes=1), allowed_owner_ids=["org-a"])
    valid = export_sealed_bundle(source, ref, public, expiry=datetime.now(timezone.utc) + timedelta(minutes=5), allowed_owner_ids=["org-a"])
    with pytest.raises(IdentityStoreError):
        import_sealed_bundle(store(tmp_path / "expired"), expired, private)
    with pytest.raises(IdentityStoreError):
        import_sealed_bundle(store(tmp_path / "wrong"), valid, other_private)
    malformed = json.dumps({"format": "FUTURE", "format_version": 99}).encode()
    with pytest.raises(IdentityStoreError):
        import_sealed_bundle(store(tmp_path / "malformed"), malformed, private)
    tampered = bytearray(valid)
    tampered[-3] ^= 1
    with pytest.raises(IdentityStoreError):
        import_sealed_bundle(store(tmp_path / "tampered"), bytes(tampered), private)


def test_protocol_rejects_stale_and_unknown_operations():
    kwargs = dict(request_id=uuid4(), sent_at=datetime.now(timezone.utc) - timedelta(hours=1), account_id=uuid4(), identity_ref=uuid4(), owner_identifier="org-a", operation=ExecutionOperation.ACCOUNT_INVENTORY, slot_key="slot")
    req = TelegramExecutionRequestV1(**kwargs)
    with pytest.raises(ValueError, match="REPLAY_REJECTED"):
        req.assert_supported()
    with pytest.raises(ValueError):
        TelegramExecutionRequestV1(**{**kwargs, "operation": "GET_URL", "sent_at": datetime.now(timezone.utc)})


def test_no_database_or_redis_settings_required(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    s = store(tmp_path)
    assert s.root.exists()


def test_provider_origin_allowlist_fails_closed():
    assert assert_telegram_url("https://ads.telegram.org/account")
    with pytest.raises(ValueError, match="PROVIDER_ORIGIN_NOT_ALLOWED"):
        assert_telegram_url("https://example.invalid/")
