"""Bounded Mail.ru Cloud WebDAV adapter for deduplicated creative media."""
from __future__ import annotations

import hashlib
import asyncio
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote, urljoin

import httpx

from app.core.config import settings

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_ROOT_SEGMENT = re.compile(r"^[A-Za-z0-9._ -]+$")
MEDIA_STORAGE_ROOTS = {"images": "images", "videos": "videos", "other": "other"}


class CreativeStorageError(RuntimeError):
    pass


class WebDavOperationError(CreativeStorageError):
    """A safe, bounded WebDAV failure description (never contains credentials)."""

    def __init__(self, reason: str, details: dict[str, object]):
        super().__init__(reason)
        self.reason = reason
        self.details = details


@dataclass(frozen=True)
class MediaDescriptor:
    content_hash: str
    mime_type: str
    size: int
    key: str


def _media_category(*, extension: str, mime_type: str | None = None) -> str:
    if (mime_type or "").lower().startswith("image/") or extension.lower() in {"png", "jpg", "jpeg", "gif", "webp", "svg", "avif"}:
        return "images"
    if (mime_type or "").lower().startswith("video/") or extension.lower() in {"mp4", "mov", "webm", "avi", "mkv"}:
        return "videos"
    return "other"


def media_key(content_hash: str, extension: str = "bin", *, mime_type: str | None = None) -> str:
    digest = str(content_hash).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("CONTENT_HASH_INVALID")
    extension = extension.lower().lstrip(".") or "bin"
    if not _SAFE_SEGMENT.fullmatch(extension):
        raise ValueError("MEDIA_EXTENSION_INVALID")
    root = (settings.mail_cloud_creative_root or "/MaxiBot/ADS/creatives").strip().strip("/")
    root_segments = root.split("/")
    if any(not _SAFE_ROOT_SEGMENT.fullmatch(segment) or segment in {".", ".."} for segment in root_segments):
        raise ValueError("STORAGE_PATH_INVALID")
    category = MEDIA_STORAGE_ROOTS[_media_category(extension=extension, mime_type=mime_type)]
    segments = root_segments + [category, f"{digest}.{extension}"]
    return "/" + "/".join(segments)


def describe_media(content: bytes, *, mime_type: str, extension: str = "bin") -> MediaDescriptor:
    if not isinstance(content, bytes) or not content:
        raise ValueError("MEDIA_EMPTY")
    if len(content) > settings.mail_cloud_max_media_bytes:
        raise ValueError("MEDIA_TOO_LARGE")
    return MediaDescriptor(hashlib.sha256(content).hexdigest(), mime_type, len(content), media_key(hashlib.sha256(content).hexdigest(), extension, mime_type=mime_type))


class MailCloudWebDav:
    provider = "MAIL_RU_WEBDAV"

    def __init__(self, *, client: httpx.AsyncClient | None = None):
        self.enabled = bool(settings.mail_cloud_enabled and settings.mail_cloud_webdav_url and settings.mail_cloud_login and settings.mail_cloud_app_password)
        self._base = (settings.mail_cloud_webdav_url or "").rstrip("/") + "/"
        self._client = client or httpx.AsyncClient(
            auth=(settings.mail_cloud_login or "", settings.mail_cloud_app_password or ""),
            timeout=httpx.Timeout(settings.mail_cloud_timeout_seconds),
            verify=True,
            follow_redirects=True,
        )
        self._owned_client = client is None
        self.diagnostics: list[dict[str, object]] = []

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    def _url(self, key: str) -> str:
        if not key.startswith("/") or ".." in key.split("/"):
            raise ValueError("STORAGE_PATH_TRAVERSAL")
        return urljoin(self._base, quote(key.lstrip("/"), safe="/"))

    def _record(self, operation: str, key: str, *, response: httpx.Response | None = None,
                response_class: str | None = None, retryable: bool = False) -> dict[str, object]:
        status = response.status_code if response is not None else None
        if status is not None:
            response_class = f"{status // 100}xx"
        details = {
            "operation": operation,
            "safe_path": key,
            "http_status": status,
            "content_length": response.headers.get("Content-Length") if response is not None else None,
            "response_class": response_class or "network",
            "retryable": retryable,
        }
        self.diagnostics.append(details)
        return details

    @staticmethod
    def _error_reason(operation: str, status: int | None) -> tuple[str, bool]:
        if status in {401, 403}:
            return "AUTH_FAILED", False
        if status in {408, 425, 429} or (status is not None and status >= 500):
            return "TIMEOUT" if status == 408 else "TRANSIENT_ERROR", True
        if status == 404:
            return "REMOTE_MISSING", False
        return {
            "MKCOL": "DIRECTORY_CREATE_FAILED",
            "PUT": "PUT_FAILED",
            "GET": "VERIFY_FAILED",
            "PROPFIND": "VERIFY_FAILED",
            "DELETE": "VERIFY_FAILED",
        }.get(operation, "VERIFY_FAILED"), False

    def _raise_response(self, operation: str, key: str, response: httpx.Response) -> None:
        reason, retryable = self._error_reason(operation, response.status_code)
        details = self._record(operation, key, response=response, retryable=retryable)
        raise WebDavOperationError(reason, details)

    def _raise_network(self, operation: str, key: str, exc: Exception) -> None:
        details = self._record(operation, key, response_class="network", retryable=True)
        raise WebDavOperationError("TIMEOUT" if isinstance(exc, (httpx.TimeoutException,)) else "TRANSIENT_ERROR", details) from exc

    async def ensure_directory(self, key: str) -> None:
        if not self.enabled:
            raise CreativeStorageError("MAIL_CLOUD_NOT_CONFIGURED")
        existing = await self.stat(key)
        if existing is not None:
            return
        try:
            response = await self._client.request("MKCOL", self._url(key))
        except httpx.HTTPError as exc:
            self._raise_network("MKCOL", key, exc)
        if response.status_code not in {201, 405}:
            self._raise_response("MKCOL", key, response)
        self._record("MKCOL", key, response=response)
        if await self.stat(key) is None:
            details = self._record("PROPFIND", key, response_class="2xx", retryable=True)
            raise WebDavOperationError("REMOTE_MISSING", details)

    async def stat(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        response = await self._client.request("PROPFIND", self._url(key), headers={"Depth": "0"})
        if response.status_code == 404:
            self._record("PROPFIND", key, response=response)
            return None
        if response.is_error:
            self._raise_response("PROPFIND", key, response)
        self._record("PROPFIND", key, response=response)
        resource_length = None
        try:
            document = ET.fromstring(response.content)
            for element in document.iter():
                if element.tag.rsplit("}", 1)[-1].lower() == "getcontentlength":
                    resource_length = element.text
                    break
        except ET.ParseError:
            # Some WebDAV-compatible endpoints return an empty 207 body.  In
            # that case size is unknown and the subsequent GET/hash check is
            # the authoritative verification.
            pass
        return {"status_code": response.status_code, "content_length": resource_length}

    async def _verify_download(self, key: str, content: bytes) -> None:
        try:
            response = await self._client.get(self._url(key))
        except httpx.HTTPError as exc:
            self._raise_network("GET", key, exc)
        if response.is_error:
            self._raise_response("GET", key, response)
        self._record("GET", key, response=response)
        if len(response.content) != len(content):
            details = self._record("GET", key, response=response, retryable=False)
            raise WebDavOperationError("SIZE_MISMATCH", details)
        if hashlib.sha256(response.content).hexdigest() != hashlib.sha256(content).hexdigest():
            details = self._record("GET", key, response=response, retryable=False)
            raise WebDavOperationError("HASH_MISMATCH", details)

    async def put_verified(self, key: str, content: bytes, *, content_type: str) -> MediaDescriptor:
        descriptor = describe_media(content, mime_type=content_type, extension=key.rsplit(".", 1)[-1] if "." in key else "bin")
        if not self.enabled:
            raise CreativeStorageError("MAIL_CLOUD_NOT_CONFIGURED")
        last_error: Exception | None = None
        for attempt in range(settings.mail_cloud_retry_attempts + 1):
            try:
                existing = await self.stat(key)
                # WebDAV servers commonly omit Content-Length for a successful
                # Depth: 0 PROPFIND.  The content-addressed key is already the
                # exact SHA-256 identity, so an existing object is a safe
                # dedupe hit even when that optional header is absent.
                if existing and (not existing.get("content_length") or str(existing.get("content_length")) == str(len(content))):
                    return descriptor
                parts = key.strip("/").split("/")
                for index in range(1, len(parts)):
                    await self.ensure_directory("/" + "/".join(parts[:index]))
                try:
                    response = await self._client.put(self._url(key), content=content, headers={"Content-Type": content_type})
                except httpx.HTTPError as exc:
                    self._raise_network("PUT", key, exc)
                if response.is_error:
                    self._raise_response("PUT", key, response)
                self._record("PUT", key, response=response)
                verified = await self.stat(key)
                if not verified or (verified.get("content_length") and int(verified["content_length"]) != len(content)):
                    if verified and verified.get("content_length"):
                        details = self._record("PROPFIND", key, response_class="2xx", retryable=False)
                        raise WebDavOperationError("SIZE_MISMATCH", details)
                    details = self._record("PROPFIND", key, response_class="2xx", retryable=True)
                    raise WebDavOperationError("REMOTE_MISSING", details)
                await self._verify_download(key, content)
                return descriptor
            except (httpx.HTTPError, CreativeStorageError, ValueError) as exc:
                last_error = exc
                if attempt < settings.mail_cloud_retry_attempts:
                    await asyncio.sleep(settings.mail_cloud_retry_backoff_seconds * (attempt + 1))
        if isinstance(last_error, WebDavOperationError):
            raise last_error
        raise CreativeStorageError(type(last_error).__name__ if last_error else "WEBDAV_UPLOAD_FAILED")

    async def copy_legacy_verified(self, old_key: str, new_key: str, *, expected_hash: str, expected_size: int, content_type: str) -> MediaDescriptor:
        """Copy a legacy object and verify both bytes; never deletes the source."""
        if not self.enabled:
            raise CreativeStorageError("MAIL_CLOUD_NOT_CONFIGURED")
        response = await self._client.get(self._url(old_key))
        if response.is_error:
            raise CreativeStorageError(f"WEBDAV_GET_{response.status_code}")
        content = response.content
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash.lower():
            raise CreativeStorageError("LEGACY_MEDIA_VERIFY_FAILED")
        descriptor = await self.put_verified(new_key, content, content_type=content_type)
        verification = await self._client.get(self._url(new_key))
        if verification.is_error or len(verification.content) != expected_size or hashlib.sha256(verification.content).hexdigest() != expected_hash.lower():
            raise CreativeStorageError("NEW_MEDIA_VERIFY_FAILED")
        return descriptor


async def store_media_if_available(
    content: bytes | None, *, mime_type: str | None, extension: str = "bin"
) -> dict[str, object]:
    """Archive verified bytes when a provider actually supplied them.

    This helper is deliberately fail-open for ADS collection: an unavailable
    archive changes the creative state, never the collection result.
    """
    if not content:
        return {"state": "SOURCE_UNAVAILABLE", "storage_key": None, "file_size": None}
    descriptor = describe_media(content, mime_type=mime_type or "application/octet-stream", extension=extension)
    storage = MailCloudWebDav()
    try:
        if not storage.enabled:
            return {"state": "AVAILABLE_NOT_STORED", "storage_key": descriptor.key, "file_size": descriptor.size, "content_hash": descriptor.content_hash}
        await storage.put_verified(descriptor.key, content, content_type=descriptor.mime_type)
        return {"state": "STORED", "storage_key": descriptor.key, "file_size": descriptor.size, "content_hash": descriptor.content_hash}
    except Exception as exc:
        safe_error = exc.reason if isinstance(exc, WebDavOperationError) else type(exc).__name__
        result = {"state": "FAILED", "storage_key": descriptor.key, "file_size": descriptor.size, "content_hash": descriptor.content_hash, "error": safe_error}
        if isinstance(exc, WebDavOperationError):
            result["diagnostic"] = exc.details
        return result
    finally:
        await storage.close()
