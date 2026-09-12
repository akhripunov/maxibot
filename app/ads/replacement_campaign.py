"""Safe replacement-campaign planning for report-only Yandex campaigns.

The legacy campaign represented by the Direct reports API is deliberately not
treated as writable.  This module builds a deterministic, owner-gated
replacement plan and provides a mockable provider workflow for tests.  The
default workflow is a plan only; no provider mutation is possible unless a
caller explicitly supplies ``live=True`` and an owner-approved write adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select

from app.db.models import AdsCampaign, AdsPromotionCampaignLink


LEGACY_CAMPAIGN_ID = "713637226"
LEGACY_CAMPAIGN_NAME = "msk365_mk_msk_conv_test1"
REPLACEMENT_CAMPAIGN_TYPE = "TEXT_CAMPAIGN"


@dataclass(frozen=True)
class LegacyCampaignAudit:
    campaign_id: str
    campaign_name: str
    report_visible: bool
    campaigns_v5_visible: bool
    campaigns_v501_visible: bool
    adgroups_visible: bool
    ads_visible: bool

    @property
    def directly_manageable(self) -> bool:
        return bool(
            self.campaigns_v5_visible
            or self.campaigns_v501_visible
            and self.adgroups_visible
        )

    @property
    def product_type(self) -> str:
        if self.report_visible and not self.campaigns_v5_visible and not self.campaigns_v501_visible:
            return "MASTER_CAMPAIGN_OR_WIZARD_REPORT_ONLY"
        return "UNKNOWN"

    def as_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "report_visible": self.report_visible,
            "campaigns_v5_visible": self.campaigns_v5_visible,
            "campaigns_v501_visible": self.campaigns_v501_visible,
            "adgroups_visible": self.adgroups_visible,
            "ads_visible": self.ads_visible,
            "product_type": self.product_type,
            "directly_manageable": self.directly_manageable,
        }


@dataclass(frozen=True)
class ReplacementCampaignSpec:
    name: str
    campaign_type: str
    objective: str
    landing_url: str
    geo: tuple[str, ...]
    strategy: dict[str, Any]
    budget: dict[str, Any]
    attribution_url: str
    ad_group_name: str
    unknown_fields: tuple[str, ...] = ()

    def campaign_payload(self) -> dict[str, Any]:
        payload = {
            "Name": self.name,
            "Type": self.campaign_type,
            "State": "OFF",
            "Strategy": self.strategy,
            "DailyBudget": self.budget,
            "TrackingParams": {"landing_url": self.attribution_url},
        }
        return payload

    def ad_group_payload(self, *, campaign_id: str | None = None) -> dict[str, Any]:
        payload = {"Name": self.ad_group_name, "RegionIds": list(self.geo)}
        if campaign_id:
            payload["CampaignId"] = str(campaign_id)
        return payload

    def initial_ad_payload(self, *, campaign_id: str | None = None) -> dict[str, Any]:
        payload = {
            "Href": self.attribution_url,
            "TextAd": {"Title": self.name[:56], "Text": "Проверка рекламного объявления"},
        }
        if campaign_id:
            payload["CampaignId"] = str(campaign_id)
        return payload


def attribution_url(landing_url: str, *, campaign_name: str) -> str:
    """Preserve the landing URL and add deterministic Yandex attribution tags."""
    parsed = urlsplit(str(landing_url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("REPLACEMENT_LANDING_URL_INVALID")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("utm_source", "yandex_direct")
    query.setdefault("utm_medium", "cpc")
    query.setdefault("utm_campaign", campaign_name)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def build_replacement_spec(*, name: str, landing_url: str, objective: str,
                           geo: list[str] | tuple[str, ...],
                           strategy: dict[str, Any], budget: dict[str, Any],
                           ad_group_name: str | None = None,
                           unknown_fields: list[str] | tuple[str, ...] = ()) -> ReplacementCampaignSpec:
    """Build only from explicitly supplied values; unknown settings stay visible."""
    if not str(name).strip() or not str(objective).strip():
        raise ValueError("REPLACEMENT_REQUIRED_FIELDS_MISSING")
    return ReplacementCampaignSpec(
        name=str(name).strip(), campaign_type=REPLACEMENT_CAMPAIGN_TYPE,
        objective=str(objective).strip(), landing_url=str(landing_url).strip(),
        geo=tuple(str(item) for item in geo), strategy=dict(strategy), budget=dict(budget),
        attribution_url=attribution_url(landing_url, campaign_name=str(name).strip()),
        ad_group_name=ad_group_name or f"{name} · тестовая группа",
        unknown_fields=tuple(str(item) for item in unknown_fields),
    )


class ReplacementProvider(Protocol):
    async def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def reread_campaign(self, campaign_id: str) -> dict[str, Any]: ...
    async def create_ad_group(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def reread_ad_group(self, ad_group_id: str) -> dict[str, Any]: ...
    async def create_ad(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def reread_ad(self, ad_id: str) -> dict[str, Any]: ...


class YandexReplacementProvider:
    """Thin Control-Plane provider adapter for the supported Direct model.

    It is intentionally not constructed by normal UI code.  The caller must
    use an explicitly write-enabled Yandex client and the owner-gated
    ``provision_replacement(..., live=True)`` path.
    """

    def __init__(self, client: Any):
        self.client = client

    async def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self.client.mutate("campaigns", method="add", params={"Campaigns": [payload]})
        return _first_result(result, "CAMPAIGN")

    async def reread_campaign(self, campaign_id: str) -> dict[str, Any]:
        row = await self.client.get_campaign(int(campaign_id))
        if not row:
            raise RuntimeError("REPLACEMENT_CAMPAIGN_REREAD_FAILED")
        return row

    async def create_ad_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self.client.mutate("adgroups", method="add", params={"AdGroups": [payload]})
        return _first_result(result, "AD_GROUP")

    async def reread_ad_group(self, ad_group_id: str) -> dict[str, Any]:
        rows = await self.client.get_ad_groups_by_ids([ad_group_id])
        row = next((item for item in rows if str(item.get("Id")) == str(ad_group_id)), None)
        if not row:
            raise RuntimeError("REPLACEMENT_AD_GROUP_REREAD_FAILED")
        return row

    async def create_ad(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self.client.mutate("ads", method="add", params={"Ads": [payload]})
        return _first_result(result, "AD")

    async def reread_ad(self, ad_id: str) -> dict[str, Any]:
        rows = await self.client.get_ads_by_ids([ad_id])
        row = next((item for item in rows if str(item.get("Id")) == str(ad_id)), None)
        if not row:
            raise RuntimeError("REPLACEMENT_AD_REREAD_FAILED")
        return row


def _first_result(result: dict[str, Any], kind: str) -> dict[str, Any]:
    rows = result.get("AddResults") or result.get("addResults") or result.get("Results") or result.get("results") or []
    if not rows or not isinstance(rows[0], dict):
        raise RuntimeError(f"REPLACEMENT_{kind}_CREATE_RESPONSE_INVALID")
    row = rows[0]
    if not row.get("Id") and not row.get("id"):
        raise RuntimeError(f"REPLACEMENT_{kind}_ID_MISSING")
    return row


async def provision_replacement(*, spec: ReplacementCampaignSpec,
                                provider: ReplacementProvider,
                                owner_approved: bool = False,
                                live: bool = False) -> dict[str, Any]:
    """Provision through a mockable adapter; live writes require two gates.

    Production callers must keep ``live=False`` until the separate OWNER
    cutover command.  The mock E2E path uses the same structured sequence.
    """
    if live and not owner_approved:
        raise PermissionError("REPLACEMENT_OWNER_APPROVAL_REQUIRED")
    if spec.unknown_fields:
        return {
            "status": "BLOCKED",
            "reason": "REPLACEMENT_SPEC_HAS_UNPROVEN_FIELDS",
            "unknown_fields": list(spec.unknown_fields),
            "old_campaign_write": "DENIED",
        }
    if not live:
        return {
            "status": "PLANNED",
            "old_campaign_write": "DENIED",
            "new_campaign_write": "READY_BUT_DISABLED",
            "steps": ["CREATE_CAMPAIGN", "REREAD_CAMPAIGN", "CREATE_AD_GROUP", "REREAD_AD_GROUP", "CREATE_AD", "REREAD_AD"],
            "campaign_payload": spec.campaign_payload(),
            "attribution_url": spec.attribution_url,
        }
    campaign = await provider.create_campaign(spec.campaign_payload())
    campaign_id = str(campaign.get("Id") or campaign.get("id") or "")
    if not campaign_id:
        raise RuntimeError("REPLACEMENT_CAMPAIGN_ID_MISSING")
    campaign_after = await provider.reread_campaign(campaign_id)
    group = await provider.create_ad_group(spec.ad_group_payload(campaign_id=campaign_id))
    group_id = str(group.get("Id") or group.get("id") or "")
    if not group_id:
        raise RuntimeError("REPLACEMENT_AD_GROUP_ID_MISSING")
    group_after = await provider.reread_ad_group(group_id)
    ad = await provider.create_ad({**spec.initial_ad_payload(campaign_id=campaign_id), "AdGroupId": group_id})
    ad_id = str(ad.get("Id") or ad.get("id") or "")
    if not ad_id:
        raise RuntimeError("REPLACEMENT_AD_ID_MISSING")
    ad_after = await provider.reread_ad(ad_id)
    return {
        "status": "PROVISIONED",
        "old_campaign_id": LEGACY_CAMPAIGN_ID,
        "old_campaign_write": "DENIED",
        "new_campaign_write": "READY_BUT_DISABLED",
        "campaign": {"before": campaign, "after": campaign_after},
        "ad_group": {"before": group, "after": group_after},
        "ad": {"before": ad, "after": ad_after},
        "attribution_url": spec.attribution_url,
        "autopilot_enable_requires_owner": True,
    }


def cutover_plan(*, new_campaign_id: str | None = None) -> list[str]:
    target = new_campaign_id or "<new-provider-campaign-id>"
    return [
        "create new campaign paused",
        "create ad group(s) and initial ad(s)",
        "wait for moderation and reread provider state",
        "verify landing, UTM/yclid and Metrica attribution",
        "verify Control Plane capabilities and rollback",
        f"explicitly authorize {target} for Autopilot",
        "start traffic only after owner approval",
        f"keep legacy {LEGACY_CAMPAIGN_ID} read-only",
    ]


async def persist_replacement_campaign(*, db: Any, account_id: str,
                                       object_type: str, object_id: str,
                                       provider_campaign: dict[str, Any],
                                       spec: ReplacementCampaignSpec) -> AdsCampaign:
    """Persist a provisioned campaign using the existing ADS tables only.

    This function is intentionally separate from provider provisioning and is
    not called by the default plan-only path.  The new link stays ADVISORY
    until the owner explicitly authorizes the cutover; legacy links are never
    deleted and are forced read-only.
    """
    external_id = str(provider_campaign.get("Id") or provider_campaign.get("id") or "")
    if not external_id:
        raise ValueError("REPLACEMENT_CAMPAIGN_ID_MISSING")
    if object_type not in {"CHANNEL", "BOT"}:
        raise ValueError("REPLACEMENT_OBJECT_TYPE_INVALID")
    campaign = AdsCampaign(
        account_id=account_id, ads_account_id=account_id, external_id=external_id,
        external_campaign_id=external_id, name=spec.name, campaign_type=spec.campaign_type,
        state="OFF", status="DRAFT", strategy_type=str(spec.strategy.get("type") or ""),
        daily_budget=float(spec.budget.get("Amount")) if spec.budget.get("Amount") is not None else None,
        provider_metadata_json=json.dumps({
            "replacement_for_campaign_id": LEGACY_CAMPAIGN_ID,
            "replacement_status": "READY_BUT_DISABLED",
            "attribution_url": spec.attribution_url,
            "unknown_fields": list(spec.unknown_fields),
        }, ensure_ascii=False, sort_keys=True),
    )
    db.add(campaign)
    await db.flush()
    predicate = (
        AdsPromotionCampaignLink.channel_id == object_id
        if object_type == "CHANNEL"
        else AdsPromotionCampaignLink.bot_promotion_profile_id == object_id
    )
    legacy = list((await db.scalars(select(AdsPromotionCampaignLink).where(
        AdsPromotionCampaignLink.object_type == object_type,
        predicate,
        AdsPromotionCampaignLink.campaign_id != campaign.id,
    ))).all())
    for link in legacy:
        link.execution_mode = "ADVISORY"
    link = AdsPromotionCampaignLink(
        object_type=object_type,
        channel_id=object_id if object_type == "CHANNEL" else None,
        bot_promotion_profile_id=object_id if object_type == "BOT" else None,
        campaign_id=campaign.id, status="ACTIVE", execution_mode="ADVISORY",
        autopilot_config_json="{}",
    )
    db.add(link)
    await db.flush()
    return campaign
