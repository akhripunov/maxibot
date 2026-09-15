"""Encrypted local identity store for the future EU executor.

It is an operational secret store, not an ADS database.  The store can run
with no DATABASE_URL/REDIS_URL and never returns or logs plaintext sessions.
"""
from __future__ import annotations

import json
import os
import tempfile
import base64
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class IdentityStoreError(RuntimeError):
    pass


class IdentityStore:
    def __init__(self, root: str | Path, master_key_file: str | Path):
        self.root = Path(root)
        self.key_file = Path(master_key_file)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _fernet(self) -> Fernet:
        try:
            key = self.key_file.read_bytes().strip()
        except OSError as exc:
            raise IdentityStoreError("IDENTITY_STORE_NOT_READY") from exc
        try:
            return Fernet(key)
        except Exception as exc:
            raise IdentityStoreError("IDENTITY_MASTER_KEY_INVALID") from exc

    def _path(self, identity_ref: UUID | str) -> Path:
        ref = str(identity_ref)
        UUID(ref)
        return self.root / f"{ref}.identity"

    def put(self, identity_ref: UUID | str, session: dict[str, str], *, allowed_owner_identifiers: list[str], format_version: int = 1, replace: bool = False) -> None:
        if not isinstance(session, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in session.items()):
            raise IdentityStoreError("IDENTITY_SESSION_INVALID")
        owners = sorted({str(x).strip() for x in allowed_owner_identifiers if str(x).strip()})
        if not owners:
            raise IdentityStoreError("IDENTITY_OWNER_ALLOWLIST_EMPTY")
        payload = {"identity_ref": str(identity_ref), "allowed_owner_identifiers": owners, "format_version": format_version, "updated_at": datetime.now(timezone.utc).isoformat(), "session": session}
        encrypted = self._fernet().encrypt(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        target = self._path(identity_ref)
        if target.exists() and not replace:
            raise IdentityStoreError("IDENTITY_ALREADY_EXISTS")
        fd, tmp = tempfile.mkstemp(prefix=".identity-", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            os.chmod(target, 0o600)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get(self, identity_ref: UUID | str) -> dict[str, Any]:
        try:
            encrypted = self._path(identity_ref).read_bytes()
            payload = json.loads(self._fernet().decrypt(encrypted))
        except (OSError, InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise IdentityStoreError("IDENTITY_NOT_PROVISIONED") from exc
        if payload.get("identity_ref") != str(identity_ref) or not isinstance(payload.get("session"), dict):
            raise IdentityStoreError("IDENTITY_STORE_CORRUPT")
        return payload

    def status(self, identity_ref: UUID | str, owner_identifier: str) -> dict[str, Any]:
        try:
            payload = self.get(identity_ref)
        except IdentityStoreError:
            return {"exists": False, "owner_allowed": False, "store_format_version": None}
        owners = payload.get("allowed_owner_identifiers", [])
        return {"exists": True, "owner_allowed": owner_identifier in owners, "store_format_version": payload.get("format_version")}


def export_sealed_bundle(store: IdentityStore, identity_ref: UUID | str, recipient_public_key_pem: bytes, *, expiry: datetime, allowed_owner_ids: list[str]) -> bytes:
    created_at = datetime.now(timezone.utc)
    recipient = serialization.load_pem_public_key(recipient_public_key_pem)
    recipient_key_id = hashlib.sha256(recipient.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    payload = {"format_version": 1, "identity_ref": str(identity_ref), "allowed_owner_identifiers": allowed_owner_ids, "created_at": created_at.isoformat(), "expires_at": expiry.astimezone(timezone.utc).isoformat(), "recipient_key_id": recipient_key_id, "session": store.get(identity_ref)["session"]}
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    data_key = Fernet.generate_key()
    sealed = Fernet(data_key).encrypt(plaintext)
    wrapped_key = recipient.encrypt(data_key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return json.dumps({"format": "MAXIBOT_TELEGRAM_IDENTITY_BUNDLE_V1", "format_version": 1, "recipient_key_id": recipient_key_id, "wrapped_key": base64.b64encode(wrapped_key).decode(), "sealed_payload": base64.b64encode(sealed).decode()}, separators=(",", ":")).encode()


def import_sealed_bundle(store: IdentityStore, bundle: bytes, recipient_private_key_pem: bytes, *, now: datetime | None = None, replace: bool = False) -> UUID:
    private = serialization.load_pem_private_key(recipient_private_key_pem, password=None)
    try:
        envelope = json.loads(bundle)
        wrapped_key = base64.b64decode(envelope["wrapped_key"], validate=True)
        sealed = base64.b64decode(envelope["sealed_payload"], validate=True)
        data_key = private.decrypt(wrapped_key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        payload = json.loads(Fernet(data_key).decrypt(sealed))
        if envelope.get("format") != "MAXIBOT_TELEGRAM_IDENTITY_BUNDLE_V1" or envelope.get("format_version") != 1 or payload.get("format_version") != 1:
            raise IdentityStoreError("IDENTITY_BUNDLE_VERSION_UNSUPPORTED")
        public = private.public_key()
        key_id = hashlib.sha256(public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        if envelope.get("recipient_key_id") != key_id or payload.get("recipient_key_id") != key_id:
            raise IdentityStoreError("IDENTITY_BUNDLE_RECIPIENT_MISMATCH")
        for field in ("identity_ref", "allowed_owner_identifiers", "created_at", "expires_at", "session"):
            if field not in payload:
                raise IdentityStoreError("IDENTITY_BUNDLE_MALFORMED")
        ref = UUID(payload["identity_ref"])
        if not isinstance(payload["allowed_owner_identifiers"], list) or not payload["allowed_owner_identifiers"]:
            raise IdentityStoreError("IDENTITY_BUNDLE_MALFORMED")
        created = datetime.fromisoformat(payload["created_at"])
        expires = datetime.fromisoformat(payload["expires_at"])
        if created.tzinfo is None or expires.tzinfo is None:
            raise IdentityStoreError("IDENTITY_BUNDLE_MALFORMED")
        if expires <= created:
            raise IdentityStoreError("IDENTITY_BUNDLE_MALFORMED")
        if expires <= (now or datetime.now(timezone.utc)):
            raise IdentityStoreError("IDENTITY_BUNDLE_EXPIRED")
        store.put(ref, payload["session"], allowed_owner_identifiers=payload["allowed_owner_identifiers"], replace=replace)
        return ref
    except IdentityStoreError:
        raise
    except Exception as exc:
        raise IdentityStoreError("IDENTITY_BUNDLE_INVALID") from exc
