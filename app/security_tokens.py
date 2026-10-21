from __future__ import annotations

import base64
import hashlib
import re
from collections import Counter
from cryptography.fernet import Fernet

from app.core.config import settings

_APP_SECRET_PLACEHOLDERS = (
    "devsecret", "developmentsecret", "changeme", "changeit", "replaceme",
    "replacewith", "yoursecret", "defaultsecret", "placeholder", "example",
    "password", "testsecret", "insecure", "secret",
)


def _weak_app_secret(value: str) -> bool:
    secret = value.strip()
    if not secret or len(secret.encode("utf-8")) < 32:
        return True
    counts = Counter(secret)
    if len(counts) < 12 or max(counts.values()) * 2 > len(secret):
        return True
    normalized = re.sub(r"[^a-z0-9]", "", secret.lower())
    return any(marker in normalized for marker in _APP_SECRET_PLACEHOLDERS)

def _fernet() -> Fernet:
    configured = (settings.custom_bot_token_encryption_key or "").strip()
    if configured:
        raw = configured.encode()
        try:
            return Fernet(raw)
        except Exception as exc:
            raise ValueError("CUSTOM_BOT_TOKEN_ENCRYPTION_KEY is invalid") from exc
    # Backward-compatible fallback for installations that have not yet added
    # the optional key. The token is still encrypted at rest and the key is
    # derived from the existing application secret, never stored in DB.
    digest = hashlib.sha256(settings.app_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def validate_crypto_configuration(*, real_mode: bool, app_secret: str, encryption_key: str | None) -> None:
    """Reject weak-pattern secrets before database/provider work in real mode."""
    if encryption_key and encryption_key.strip():
        try:
            Fernet(encryption_key.strip().encode())
        except Exception as exc:
            raise RuntimeError("CUSTOM_BOT_TOKEN_ENCRYPTION_KEY is invalid") from exc
    if real_mode and _weak_app_secret(app_secret):
        raise RuntimeError("APP_SECRET is too short, low-variety, blank, or a known placeholder in real mode")


def validate_crypto_settings() -> None:
    """Validate the active application's crypto settings before runtime work."""
    validate_crypto_configuration(
        real_mode=settings.is_real_max,
        app_secret=settings.app_secret,
        encryption_key=settings.custom_bot_token_encryption_key,
    )

def encrypt_token(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()

def decrypt_token(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
