from __future__ import annotations

import base64
import hashlib
from cryptography.fernet import Fernet

from app.core.config import settings

def _fernet() -> Fernet:
    configured = (settings.custom_bot_token_encryption_key or "").strip()
    if configured:
        raw = configured.encode()
        try:
            return Fernet(raw)
        except Exception:
            pass
    # Backward-compatible fallback for installations that have not yet added
    # the optional key. The token is still encrypted at rest and the key is
    # derived from the existing application secret, never stored in DB.
    digest = hashlib.sha256(settings.app_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))

def encrypt_token(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()

def decrypt_token(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
