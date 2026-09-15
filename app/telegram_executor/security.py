"""Transport/security guards for the standalone executor."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

ALLOWED_TELEGRAM_ORIGINS = frozenset({"ads.telegram.org"})


def assert_mtls_files(cert_file: str | None, key_file: str | None, client_ca_file: str | None) -> None:
    if not all((cert_file, key_file, client_ca_file)):
        raise RuntimeError("REMOTE_EXECUTOR_NOT_READY")
    if not all(Path(path).is_file() for path in (cert_file, key_file, client_ca_file)):
        raise RuntimeError("REMOTE_EXECUTOR_NOT_READY")


def assert_telegram_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_TELEGRAM_ORIGINS:
        raise ValueError("PROVIDER_ORIGIN_NOT_ALLOWED")
    return url

