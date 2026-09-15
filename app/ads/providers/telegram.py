"""Read-only Telegram Ads Organization provider.

Telegram Ads currently exposes an authenticated web contract rather than a
public JSON API.  This module is the only place that knows those endpoints or
HTML markers.  The rest of ADS consumes the normal :class:`ProviderReport`
boundary and therefore cannot accidentally depend on cookies or selectors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import re
from typing import Any
from urllib.parse import urljoin

import httpx

from app.ads.providers.base import AdsProvider, ProviderReport
from app.security_tokens import decrypt_token, encrypt_token

TELEGRAM_ADS_BASE_URL = "https://ads.telegram.org"
_OWNER_MARKER = re.compile(r'class=["\'][^"\']*pr-header-account-name[^"\']*["\'][^>]*>(.*?)<', re.I | re.S)
_ACCOUNT_LINK = re.compile(r'href=["\']/choose_account/([^"\']+)["\'][^>]*>(.*?)<', re.I | re.S)
_AD_LINK = re.compile(r'href=["\']/account/ad/(\d+)["\'][^>]*>(.*?)<', re.I | re.S)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


class TelegramAdsContractError(RuntimeError):
    """The undocumented provider response no longer matches our contract."""


class TelegramAdsSessionState:
    VALID = "VALID"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    UNKNOWN = "UNKNOWN"


def encrypt_session_cookies(cookies: dict[str, str]) -> str:
    """Encrypt a cookie jar for durable provider metadata storage.

    The ciphertext may be stored in ``AdsAccount.provider_metadata_json``;
    plaintext cookie values never enter SQL, logs or admin responses.
    """
    import json
    return encrypt_token(json.dumps({str(key): str(value) for key, value in cookies.items()}, sort_keys=True))


def decrypt_session_cookies(ciphertext: str) -> dict[str, str]:
    import json
    value = json.loads(decrypt_token(ciphertext))
    if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise TelegramAdsContractError("TELEGRAM_ADS_SESSION_SCHEMA_INVALID")
    return value


@dataclass(frozen=True)
class TelegramAdsOwnerContext:
    """Isolated owner scope; one cookie jar must never serve two accounts."""

    expected_external_account_id: str
    expected_display_name: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    session_state: str = TelegramAdsSessionState.UNKNOWN
    last_owner_assertion: str | None = None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def _owner_marker(html: str) -> str | None:
    match = _OWNER_MARKER.search(html or "")
    return _clean(match.group(1)) if match else None


def parse_accounts(html: str) -> list[dict[str, str]]:
    """Parse selectable account contexts; no name-only binding is performed."""
    rows = [{"external_id": key, "display_name": _clean(value)} for key, value in _ACCOUNT_LINK.findall(html or "") if _clean(value)]
    if not rows and "choose_account" in (html or ""):
        raise TelegramAdsContractError("TELEGRAM_ADS_ACCOUNT_SELECTOR_CHANGED")
    return rows


def parse_ads(html: str) -> list[dict[str, Any]]:
    """Parse account inventory, accepting only a proven provider empty state.

    Telegram renders the create-ad CTA on an account with no ads.  That CTA is
    not an inventory row and must not be mistaken for selector drift.  We only
    return an empty inventory when the authenticated owner marker, the known
    empty-state heading, and the CTA are all present and no ad rows exist.
    Unknown or contradictory markup remains fail-closed.
    """
    rows = []
    for ad_id, label in _AD_LINK.findall(html or ""):
        if _clean(label):
            rows.append({"Id": ad_id, "Name": _clean(label), "Type": "TELEGRAM_AD", "State": None, "Status": None})
    text = _clean(html).casefold()
    empty_state = (
        bool(_owner_marker(html))
        and not _AD_LINK.search(html or "")
        and "you have no ads yet" in text
        and "create a new ad" in text
    )
    if "account/ad/" in (html or "") and not rows and not empty_state:
        raise TelegramAdsContractError("TELEGRAM_ADS_AD_SELECTOR_CHANGED")
    return rows


def parse_stats(html: str) -> dict[str, float | None]:
    """Parse only explicitly labelled metrics; absent values stay ``None``."""
    text = _clean(html)
    result: dict[str, float | None] = {"Impressions": None, "Views": None, "Clicks": None, "Cost": None, "Conversions": None, "CPM": None}
    labels = {
        "Impressions": r"impressions|показы",
        "Views": r"views|просмотры",
        "Clicks": r"clicks|клики",
        "Cost": r"spend|cost|расход",
        "Conversions": r"conversions|конверсии",
        "CPM": r"cpm",
    }
    for field, label in labels.items():
        match = re.search(rf"(?:{label})\s*[:=]?\s*({_NUMBER.pattern})", text, re.I)
        if match:
            result[field] = float(match.group(1).replace(",", "."))
    return result


class TelegramAdsGateway:
    """HTTP gateway with fail-closed session and owner assertions."""

    def __init__(self, context: TelegramAdsOwnerContext, *, base_url: str = TELEGRAM_ADS_BASE_URL, client: httpx.AsyncClient | None = None):
        self.context = context
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=self.base_url, cookies=context.cookies, timeout=20, follow_redirects=False)
        self._owned_client = client is None

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def get(self, path: str) -> str:
        response = await self._client.get(urljoin(self.base_url + "/", path.lstrip("/")))
        if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location", "").startswith("/auth"):
            self.context = TelegramAdsOwnerContext(self.context.expected_external_account_id, self.context.expected_display_name, self.context.cookies, TelegramAdsSessionState.REAUTH_REQUIRED, self.context.last_owner_assertion)
            raise TelegramAdsContractError("TELEGRAM_ADS_REAUTH_REQUIRED")
        if response.status_code >= 400:
            raise TelegramAdsContractError(f"TELEGRAM_ADS_HTTP_{response.status_code}")
        if not response.text:
            raise TelegramAdsContractError("TELEGRAM_ADS_EMPTY_RESPONSE")
        return response.text

    async def assert_owner(self) -> str:
        marker = _owner_marker(await self.get("/account"))
        if not marker:
            raise TelegramAdsContractError("TELEGRAM_ADS_OWNER_MARKER_MISSING")
        expected = self.context.expected_display_name
        if expected and marker != expected:
            raise TelegramAdsContractError("TELEGRAM_ADS_OWNER_MISMATCH")
        self.context = TelegramAdsOwnerContext(self.context.expected_external_account_id, expected, self.context.cookies, TelegramAdsSessionState.VALID, marker)
        return marker

    async def discover_accounts(self) -> list[dict[str, str]]:
        """Return selectable organizations without binding by display name."""
        return parse_accounts(await self.get("/account"))

    async def select_account(self, external_account_id: str) -> str:
        """Select and immediately assert one exact opaque owner ID."""
        expected = str(external_account_id or "").strip()
        if not expected or expected != self.context.expected_external_account_id:
            raise TelegramAdsContractError("TELEGRAM_ADS_OWNER_SELECTION_MISMATCH")
        await self.get(f"/choose_account/{expected}")
        return await self.assert_owner()


class TelegramAdsProvider(AdsProvider):
    """Read-only provider adapter; all writes are intentionally unavailable."""

    provider_name = "TELEGRAM_ADS"

    def __init__(self, context: TelegramAdsOwnerContext, *, gateway: TelegramAdsGateway | None = None):
        self.gateway = gateway or TelegramAdsGateway(context)

    async def close(self) -> None:
        await self.gateway.close()

    async def campaigns(self, campaign_ids: list[int] | None = None) -> ProviderReport:
        await self.gateway.assert_owner()
        rows = parse_ads(await self.gateway.get("/account"))
        if campaign_ids:
            wanted = {str(value) for value in campaign_ids}
            rows = [row for row in rows if str(row.get("Id")) in wanted]
        return ProviderReport(self.provider_name, "CAMPAIGNS", rows, ("Id", "Name", "Type", "State", "Status"), fetched_at=datetime.now(timezone.utc))

    async def campaign_performance(self, campaign_id: int, *, date_from: date, date_to: date) -> ProviderReport:
        await self.gateway.assert_owner()
        stats = parse_stats(await self.gateway.get(f"/account/ad/{int(campaign_id)}/stats"))
        row = {"Date": date_to.isoformat(), "CampaignId": str(campaign_id), **stats}
        return ProviderReport(self.provider_name, "TELEGRAM_AD_STATS", [row], tuple(row.keys()), date_from, date_to, fetched_at=datetime.now(timezone.utc))

    async def ads(self, campaign_ids: list[int]) -> ProviderReport:
        await self.gateway.assert_owner()
        rows = parse_ads(await self.gateway.get("/account"))
        wanted = {str(value) for value in campaign_ids}
        rows = [row for row in rows if str(row.get("Id")) in wanted]
        return ProviderReport(self.provider_name, "ADS", rows, ("Id", "Name", "Type", "State", "Status"), fetched_at=datetime.now(timezone.utc))
