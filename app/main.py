import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import uuid
import httpx
from datetime import datetime, timedelta, timezone, date as date_type, time as time_type
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Header
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.db.base import Base
from app.db.session import engine, get_session
from app.db.models import Admin, Channel, Conversation, Message, BlockedUser, LandingPage, TrafficVisit, TrafficProfile, TrafficEvent, MaxIdentityLink, TrafficSubscription, TrafficMembership, TrafficAttributionCandidate, OfflineConversion, SystemPromotionProfile, BotTrafficVisit, BotMaxIdentityLink, BotTrafficEvent, BotConversion, BotConnection, AdsAiRun, SubscriptionOrder
from app.domain.services import WELCOME, SENT, editable_welcome, render_channel_welcome_message, normalize_channel_welcome, normalize_channel_sent, promotion_landing_description
from app.integrations.max_client import RealMaxClient, StubMaxClient
from app.bot.runtime import MaxBotRuntime
from app.core.config import settings
from app.security import validate_max_init_data
from app.integrations.yandex_oauth import authorize_url, combined_scope, configured, consume_state, create_state, create_system_state, exchange_code, get_encrypted_token, save_token, save_system_token
from app.integrations.yandex_metrica import ensure_channel_setup, ensure_system_setup
from app.integrations.yandex_direct import YandexDirectAPIError, YandexDirectClient
from app.db.models import now as model_now
from app.traffic.deep_link import build_bot_deep_link, build_miniapp_deep_link, build_native_miniapp_deep_link, build_web_bot_deep_link, build_web_miniapp_deep_link, new_attribution_token, valid_max_target
from app.traffic.metrica_tag import metrica_tag_html, valid_counter_id
from app.traffic.funnel_reporting import FunnelObservation, canonical_paid_click_report
from app.traffic.funnel_reporting import campaign_matches
from app.traffic.analytics import traffic_stats_for_channels
from app.traffic.join_intent import activate_join_intent
from app.security_tokens import decrypt_token
from app.integrations.robokassa import valid_result_signature
from app.integrations.subscriptions import add_subscription_months
import html
import secrets
from app.ads.codex_worker import claim_next_codex_run, fail_claimed_run, lease_active, worker_enabled, COMPLETED, FAILED, PROCESSING
from app.ads.service import persist_development_result
from app.ads.events import publish_ai_run_event

client = RealMaxClient() if settings.is_real_max else StubMaxClient()
bot_runtime: MaxBotRuntime | None = None
logger = logging.getLogger(__name__)


def _is_mobile_request(request: Request | None) -> bool:
    """Return true for handheld MAX/browser traffic, not desktop browsers."""
    if request is None:
        return False
    user_agent = (request.headers.get("user-agent") or "").lower()
    return any(marker in user_agent for marker in ("iphone", "ipad", "ipod", "android", "mobile"))


def _diagnostic_device_family(request: Request | None) -> str:
    """Return a low-cardinality device label; never persist a raw UA in events."""
    user_agent = (request.headers.get("user-agent") if request else "") or ""
    value = user_agent.lower()
    if "iphone" in value or "ipad" in value or "ipod" in value:
        return "IOS_MOBILE"
    if "android" in value:
        return "ANDROID_MOBILE"
    if "windows" in value:
        return "WINDOWS_DESKTOP"
    if "macintosh" in value or "mac os" in value:
        return "MAC_DESKTOP"
    if "linux" in value:
        return "LINUX_DESKTOP"
    return "UNKNOWN"


def _diagnostic_device_family_from_user_agent(user_agent: str | None) -> str:
    value = (user_agent or "").lower()
    if "iphone" in value or "ipad" in value or "ipod" in value:
        return "IOS_MOBILE"
    if "android" in value:
        return "ANDROID_MOBILE"
    if "windows" in value:
        return "WINDOWS_DESKTOP"
    if "macintosh" in value or "mac os" in value:
        return "MAC_DESKTOP"
    if "linux" in value:
        return "LINUX_DESKTOP"
    return "UNKNOWN"


def _diagnostic_referrer_host(request: Request | None) -> str | None:
    """Return only the referrer hostname, excluding paths and query strings."""
    if not request:
        return None
    referrer = request.headers.get("referer")
    if not referrer:
        return None
    return (urlsplit(referrer).hostname or "").lower() or None


def _diagnostic_browser_family(request: Request | None) -> str:
    """Return a low-cardinality browser label; never persist the raw UA."""
    value = ((request.headers.get("user-agent") if request else "") or "").lower()
    if "yandex" in value:
        return "YANDEX"
    if "firefox" in value or "fxios" in value:
        return "FIREFOX"
    if "edg/" in value or "edge/" in value:
        return "OTHER"
    if "chrome" in value or "crios" in value:
        return "CHROME"
    if "safari" in value and "chrome" not in value and "crios" not in value:
        return "SAFARI"
    if value:
        return "OTHER"
    return "UNKNOWN"


def _diagnostic_device_family_from_signals(signals: dict, request: Request | None = None) -> str:
    """Use server UA when present, otherwise only coarse client platform."""
    from_request = _diagnostic_device_family(request)
    if from_request != "UNKNOWN":
        return from_request
    platform = str((signals or {}).get("platform") or "").lower()
    if "iphone" in platform or "ipad" in platform or "ios" in platform:
        return "IOS_MOBILE"
    if "android" in platform:
        return "ANDROID_MOBILE"
    if "win" in platform:
        return "WINDOWS_DESKTOP"
    if "mac" in platform:
        return "MAC_DESKTOP"
    if "linux" in platform:
        return "LINUX_DESKTOP"
    return "UNKNOWN"


def _diagnostic_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]


def _safe_error_message(value: str | None) -> str | None:
    """Bound client error text and remove URL/token-like material."""
    if not value:
        return None
    cleaned = str(value).replace("\r", " ").replace("\n", " ")
    cleaned = cleaned.replace("http://", "[url]").replace("https://", "[url]")
    cleaned = cleaned.replace("max://", "[scheme]")
    cleaned = re.sub(r"(?:startapp|start_param|token|initData|yclid)\s*[=:][^\s&]+", r"\1=[redacted]", cleaned, flags=re.I)
    return cleaned[:200]


def _safe_target_metadata(target: str | None) -> dict:
    """Expose only allowlisted target shape, never query/token contents."""
    if not target:
        return {"target_scheme": None, "target_host": None, "target_path": None}
    parsed = urlsplit(target)
    scheme = parsed.scheme.lower() if parsed.scheme.lower() in {"max", "https"} else "other"
    host = (parsed.hostname or "").lower()
    allowed_hosts = {"max.ru", "web.max.ru"}
    safe_host = host if host in allowed_hosts else "other"
    path = parsed.path or "/"
    # Bot/channel paths are diagnostic only; never include a query or token.
    safe_path = path[:128] if safe_host in allowed_hosts and path.startswith("/") else None
    return {"target_scheme": scheme, "target_host": safe_host, "target_path": safe_path}


def _safe_browser_signals(signals: dict) -> dict:
    """Keep browser diagnostics bounded and free of URLs/tokens/user data."""
    allowed = {
        "visibility", "document_hidden", "navigation_type",
        "screen_width_bucket", "screen_height_bucket", "connection_type",
        "target_scheme", "target_host", "target_path", "has_startapp",
    }
    safe: dict = {}
    for key in allowed:
        value = signals.get(key)
        if isinstance(value, (str, int, float, bool)):
            safe[key] = str(value)[:64] if isinstance(value, str) else value
    return safe


def _safe_event_field(event: TrafficEvent, field: str) -> str | None:
    if not event.metadata_json:
        return None
    try:
        value = json.loads(event.metadata_json).get(field)
    except (TypeError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and len(value) <= 64 else None

async def refresh_avatar_cache() -> None:
    if not isinstance(client, RealMaxClient):
        return
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        channels = (await db.scalars(select(Channel))).all()
        conversations = (await db.scalars(select(Conversation))).all()
        changed = False
        for channel in channels:
            if channel.avatar_url:
                continue
            try:
                chat = await client.get_chat(channel.external_id)
                avatar_url = client._image_url(chat.get("icon"))
                if avatar_url:
                    channel.avatar_url = avatar_url
                    changed = True
            except Exception:
                continue
        for conversation in conversations:
            if conversation.avatar_url:
                continue
            try:
                chat = await client.get_chat(conversation.user_external_id)
                user = chat.get("dialog_with_user") or chat
                avatar_url = client._image_url(user)
                if avatar_url:
                    conversation.avatar_url = avatar_url
                    changed = True
            except Exception:
                continue
        if changed:
            await db.commit()

@asynccontextmanager
async def lifespan(app):
    global bot_runtime
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if settings.is_real_max:
        await refresh_avatar_cache()
        bot_runtime = MaxBotRuntime(client, __import__('app.db.session', fromlist=['SessionLocal']).SessionLocal)
        # Polling is opt-in: production enables it explicitly only on the
        # dedicated worker. This prevents a local API started with a copied
        # production environment from becoming a second MAX poller.
        run_polling = os.getenv("MAX_RUN_POLLING", "false").lower() in {"1", "true", "yes", "on"}
        if settings.max_updates_mode.lower() == "webhook":
            if not settings.max_webhook_url:
                raise RuntimeError("MAX_WEBHOOK_URL is required in webhook mode")
            webhook_updates = ["message_created", "message_callback", "bot_started", "bot_added", "bot_removed", "user_added", "user_removed"]
            if settings.max_join_request_signal:
                webhook_updates.append("join_request_created")
            await client.subscribe_webhook(
                settings.max_webhook_url,
                webhook_updates,
                settings.max_webhook_secret,
            )
            task = None
        elif run_polling:
            task = asyncio.create_task(bot_runtime.run_forever())
        else:
            task = None
    else:
        task = None
    try:
        yield
    finally:
        if bot_runtime:
            await bot_runtime.stop()
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if isinstance(client, RealMaxClient):
            await client.close()

app = FastAPI(title="MAXIBOT MVP", lifespan=lifespan)
mini_dir = Path(__file__).parent.parent / "miniapp"
uploads_dir = Path(settings.uploads_dir)
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/miniapp", StaticFiles(directory=mini_dir), name="miniapp")
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
public_site_file = static_dir / "public_site.html"
legal_dir = static_dir / "legal"
yandex_verification_file = static_dir / "yandex_b08d068dab89b456.html"
admin_shell_file = static_dir / "admin" / "index.html"
sitemap_file = static_dir / "sitemap.xml"
robots_file = static_dir / "robots.txt"


@app.get("/", response_class=FileResponse)
async def public_site(request: Request):
    """Public service and payment information for Robokassa review."""
    if request.url.hostname == "admin.maxibot.pro":
        return FileResponse(admin_shell_file, media_type="text/html", headers={"Cache-Control": "no-store"})
    return FileResponse(public_site_file, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/admin", response_class=FileResponse)
async def admin_shell(request: Request):
    if request.url.hostname == "admin.maxibot.pro":
        return RedirectResponse("/", status_code=308)
    return FileResponse(admin_shell_file, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/yandex_b08d068dab89b456.html", response_class=FileResponse)
async def yandex_verification():
    return FileResponse(yandex_verification_file, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/sitemap.xml", response_class=FileResponse)
async def sitemap():
    return FileResponse(sitemap_file, media_type="application/xml", headers={"Cache-Control": "no-store"})


@app.get("/robots.txt", response_class=FileResponse)
async def robots():
    return FileResponse(robots_file, media_type="text/plain", headers={"Cache-Control": "no-store"})


@app.get("/offer", response_class=FileResponse)
async def offer_page():
    return FileResponse(legal_dir / "offer.html", media_type="text/html", headers={"Cache-Control": "no-store"})

@app.get("/privacy", response_class=FileResponse)
async def privacy_page():
    return FileResponse(legal_dir / "privacy.html", media_type="text/html", headers={"Cache-Control": "no-store"})

@app.get("/payment-and-refund", response_class=FileResponse)
async def payment_and_refund_page():
    return FileResponse(legal_dir / "payment-and-refund.html", media_type="text/html", headers={"Cache-Control": "no-store"})


async def _robokassa_payload(request: Request) -> dict[str, str]:
    values = dict(request.query_params)
    if request.method == "POST":
        body = (await request.body()).decode("utf-8", errors="replace")
        for key, items in parse_qs(body, keep_blank_values=True).items():
            if items:
                values[key] = items[-1]
    return values


@app.api_route("/payments/robokassa/result", methods=["GET", "POST"])
async def robokassa_result(request: Request, db: AsyncSession = Depends(get_session)):
    payload = await _robokassa_payload(request)
    invoice_id = payload.get("InvId", "")
    amount = payload.get("OutSum", "")
    signature = payload.get("SignatureValue", "")
    if not invoice_id or not amount or not valid_result_signature(
        amount=amount, invoice_id=invoice_id, signature=signature
    ):
        raise HTTPException(status_code=400, detail="invalid Robokassa signature")
    try:
        invoice_number = int(invoice_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid invoice") from exc
    order = await db.scalar(select(SubscriptionOrder).where(SubscriptionOrder.invoice_id == invoice_number))
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status == "pending":
        order.status = "paid"
        order.paid_at = model_now()
        admin = await db.get(Admin, order.admin_id)
        if admin is None:
            raise HTTPException(status_code=404, detail="admin not found")
        admin.subscription_expires_at = add_subscription_months(
            admin.subscription_expires_at,
            order.months,
            now=order.paid_at,
        )
        await db.commit()
    return Response(content=f"OK{invoice_id}", media_type="text/plain")


@app.get("/payments/robokassa/success", response_class=HTMLResponse)
async def robokassa_success():
    return HTMLResponse("<h1>Оплата принята</h1><p>Платёж передан в MAXIBOT на подтверждение.</p>")


@app.get("/payments/robokassa/fail", response_class=HTMLResponse)
async def robokassa_fail():
    return HTMLResponse("<h1>Оплата не завершена</h1><p>Попробуйте ещё раз из MAXIBOT.</p>")

@app.get("/documents/oferta", response_class=FileResponse)
async def documents_offer_page():
    return FileResponse(legal_dir / "oferta.html", media_type="text/html", headers={"Cache-Control": "no-store"})

@app.get("/documents/politika-konfidencialnosti", response_class=FileResponse)
async def documents_privacy_page():
    return FileResponse(legal_dir / "politika-konfidencialnosti.html", media_type="text/html", headers={"Cache-Control": "no-store"})

@app.get("/documents/politika-vozvrata", response_class=FileResponse)
async def documents_refund_page():
    return FileResponse(legal_dir / "politika-vozvrata.html", media_type="text/html", headers={"Cache-Control": "no-store"})

@app.get("/tariffs", response_class=FileResponse)
@app.get("/payment", response_class=FileResponse)
@app.get("/refund", response_class=FileResponse)
@app.get("/contacts", response_class=FileResponse)
async def public_site_legacy_section():
    return FileResponse(public_site_file, media_type="text/html", headers={"Cache-Control": "no-store"})


demo_dir = Path(__file__).parent.parent / "demo"
@app.get("/demo/bot", response_class=FileResponse)
async def demo_bot():
    return FileResponse(demo_dir / "bot.html")
app.mount("/demo", StaticFiles(directory=demo_dir, html=True), name="demo")

class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)

class ReceivingIn(BaseModel):
    accepting: bool

class TemplateIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TrafficEventIn(BaseModel):
    channel_id: str | None = None


class BrowserTrafficEventIn(BaseModel):
    visitor_token: str = Field(min_length=16, max_length=96)
    event_type: str = Field(min_length=3, max_length=40)
    client_timestamp: str | None = None
    error_class: str | None = Field(default=None, max_length=64)
    short_message: str | None = Field(default=None, max_length=400)
    stage: str | None = Field(default=None, max_length=64)
    raw_signals: dict = Field(default_factory=dict)


class MetricaClientIdIn(BaseModel):
    visitor_token: str = Field(min_length=16, max_length=96)
    client_id: str = Field(min_length=5, max_length=64)


class MiniAppTrafficEventIn(BaseModel):
    event_type: str = Field(min_length=3, max_length=40)
    visibility: str | None = Field(default=None, max_length=32)


class MiniAppTelemetryIn(BaseModel):
    event_type: str = Field(min_length=3, max_length=40)
    start_param: str | None = Field(default=None, max_length=96)
    visibility: str | None = Field(default=None, max_length=32)
    client_timestamp: str | None = Field(default=None, max_length=64)
    error_class: str | None = Field(default=None, max_length=64)
    short_message: str | None = Field(default=None, max_length=400)
    stage: str | None = Field(default=None, max_length=64)
    raw_signals: dict = Field(default_factory=dict)

class CodexClaimIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=128)
    worker_release_id: str | None = Field(default=None, max_length=128)
class CodexResultIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=128)
    lease_id: str = Field(min_length=16, max_length=128)
    result: dict
    telemetry: dict = Field(default_factory=dict)
class CodexFailIn(BaseModel):
    worker_id: str = Field(min_length=3, max_length=128)
    lease_id: str = Field(min_length=16, max_length=128)
    error_code: str = Field(min_length=3, max_length=96)
    retryable: bool = True
    diagnostics: dict = Field(default_factory=dict)

def _require_codex_worker(authorization: str | None) -> None:
    configured = settings.ads_codex_worker_token
    supplied = (authorization or "").removeprefix("Bearer ")
    if not worker_enabled() or not configured or not supplied or not secrets.compare_digest(configured, supplied):
        raise HTTPException(401, "worker authentication failed")

@app.post("/internal/ads/codex/claim")
async def ads_codex_claim(payload: CodexClaimIn, authorization: str | None = Header(default=None), db: AsyncSession = Depends(get_session)):
    _require_codex_worker(authorization)
    run = await claim_next_codex_run(db, payload.worker_id, worker_release_id=payload.worker_release_id)
    if not run: return Response(status_code=204)
    return {"run_id":run.id,"lease_id":run.lease_id,"lease_expires_at":run.lease_expires_at.isoformat(),"prompt_version":run.prompt_version,"schema_version":run.schema_version,"output_schema":"campaign_diagnosis_schema_v1","safe_context":json.loads(run.safe_context_json or "{}")}

@app.post("/internal/ads/codex/runs/{run_id}/result")
async def ads_codex_result(run_id: str, payload: CodexResultIn, authorization: str | None = Header(default=None), db: AsyncSession = Depends(get_session)):
    _require_codex_worker(authorization)
    run=await db.scalar(select(AdsAiRun).where(AdsAiRun.id==run_id))
    if not run or run.status != PROCESSING or run.worker_id!=payload.worker_id or not lease_active(run,payload.lease_id): raise HTTPException(409,"stale worker lease")
    if payload.telemetry:
        run.worker_diagnostics_json = json.dumps(payload.telemetry, ensure_ascii=False, separators=(",", ":"))[:12000]
    await persist_development_result(db,channel_id=run.channel_id,output=payload.result,ai=run,worker=True)
    if run.status in {COMPLETED, FAILED}:
        try:
            await publish_ai_run_event(run.id, run.status)
        except Exception:
            logger.exception("ADS_AI_EVENT_PUBLISH_FAILED run_id=%s status=%s", run.id, run.status)
    return {"status":run.status, "validation_error": run.validation_error}

@app.post("/internal/ads/codex/runs/{run_id}/fail")
async def ads_codex_fail(run_id: str, payload: CodexFailIn, authorization: str | None = Header(default=None), db: AsyncSession = Depends(get_session)):
    _require_codex_worker(authorization)
    run=await db.scalar(select(AdsAiRun).where(AdsAiRun.id==run_id))
    if not run or run.worker_id!=payload.worker_id or not lease_active(run,payload.lease_id): raise HTTPException(409,"stale worker lease")
    await fail_claimed_run(db,run,retryable=payload.retryable,error_code=payload.error_code,diagnostics=payload.diagnostics)
    if run.status == FAILED:
        try:
            await publish_ai_run_event(run.id, run.status)
        except Exception:
            logger.exception("ADS_AI_EVENT_PUBLISH_FAILED run_id=%s status=%s", run.id, run.status)
    return {"status":run.status}


async def require_admin(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
) -> Admin:
    identity = validate_max_init_data(x_max_init_data or "")
    admin = await db.scalar(select(Admin).where(Admin.external_id == identity["external_id"]))
    if not admin:
        raise HTTPException(403, "MAX user is not an administrator")
    return admin

async def get_channel(channel_id: str, db: AsyncSession, admin_id: str | None = None) -> Channel:
    query = select(Channel).where(Channel.id == channel_id)
    if admin_id:
        query = query.where(Channel.admin_id == admin_id)
    channel = await db.scalar(query)
    if not channel:
        raise HTTPException(404, "channel not found")
    return channel


def _signed_startapp(identity: dict) -> str | None:
    """Read only a start parameter included in the already verified initData."""
    values = identity.get("values") or {}
    for key in ("start_param", "startapp", "payload"):
        value = values.get(key)
        if value:
            return str(value).strip()
    return None


async def _traffic_identity(
    x_max_init_data: str | None,
    db: AsyncSession,
) -> tuple[dict, TrafficVisit, Channel, MaxIdentityLink]:
    identity = validate_max_init_data(x_max_init_data or "")
    # These are server-confirmed telemetry markers. They are diagnostic only;
    # all existing identity/attribution decisions remain unchanged below.
    startapp = _signed_startapp(identity)
    if not startapp:
        raise HTTPException(422, "verified MAX initData has no start_param")
    visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.startapp_token == startapp))
    if not visit:
        raise HTTPException(404, "traffic startapp token not found")
    await _record_safe_telemetry(db, visit, "INIT_DATA_VALID", source="server", trusted=True)
    await _record_safe_telemetry(db, visit, "START_PARAM_RESOLVED", source="server", trusted=True)
    user_id = str(identity["external_id"])
    await _record_safe_telemetry(db, visit, "IDENTITY_RECEIVED", source="server", trusted=True)
    await _record_safe_telemetry(db, visit, "BIND_REQUEST_SENT", source="server", trusted=True)
    link = await db.scalar(select(MaxIdentityLink).where(MaxIdentityLink.visitor_id == visit.id))
    if link and link.max_user_id != user_id:
        raise HTTPException(409, "traffic visitor is already linked to another MAX user")
    if not link:
        fingerprint = hashlib.sha256((x_max_init_data or "").encode()).hexdigest()
        link = MaxIdentityLink(
            visitor_id=visit.id,
            max_user_id=user_id,
            startapp_token=startapp,
            user_name=(identity.get("user") or {}).get("name") or (identity.get("user") or {}).get("first_name"),
            init_data_fingerprint=fingerprint,
        )
        db.add(link)
    visit.max_user_id = user_id
    visit.max_opened_at = visit.max_opened_at or model_now()
    await _record_safe_telemetry(db, visit, "BIND_SUCCESS", source="server", trusted=True)
    channel = await db.get(Channel, visit.channel_id)
    if not channel:
        raise HTTPException(404, "traffic channel not found")
    return identity, visit, channel, link


async def _traffic_event(db: AsyncSession, visit: TrafficVisit, event_type: str, user_id: str, metadata: dict | None = None) -> bool:
    dedupe = f"poc:{event_type}:{visit.id}:{user_id}"
    if await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
        return False
    db.add(TrafficEvent(
        channel_id=visit.channel_id,
        visit_id=visit.id,
        max_user_id=user_id,
        event_type=event_type,
        event_time=model_now(),
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False, default=str),
        dedupe_key=dedupe,
    ))
    return True


async def _traffic_diagnostic_event(
    db: AsyncSession,
    visit: TrafficVisit,
    event_type: str,
    metadata: dict | None = None,
) -> bool:
    """Persist one safe, idempotent funnel stage without a MAX identity."""
    dedupe = f"diagnostic:{event_type}:{visit.id}"
    if await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
        return False
    db.add(TrafficEvent(
        channel_id=visit.channel_id,
        visit_id=visit.id,
        max_user_id=None,
        event_type=event_type,
        event_time=model_now(),
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False, default=str),
        dedupe_key=dedupe,
    ))
    return True

SYSTEM_TELEMETRY_EVENTS = {
    "SYSTEM_LANDING_RENDERED", "SYSTEM_REDIRECT_SENT", "SYSTEM_REDIRECT_ATTEMPTED",
    "SYSTEM_PAGE_HIDDEN", "SYSTEM_PAGE_VISIBLE_AGAIN", "SYSTEM_BOT_STARTED",
    "MAX_STARTAPP_HANDOFF",
}

async def _bot_telemetry_event(db: AsyncSession, visit: BotTrafficVisit, event_type: str, metadata: dict | None = None) -> bool:
    if event_type not in SYSTEM_TELEMETRY_EVENTS and event_type not in SYSTEM_HANDOFF_EVENTS:
        return False
    dedupe = f"bot-telemetry:{visit.id}:{event_type}"
    if await db.scalar(select(BotTrafficEvent.id).where(BotTrafficEvent.dedupe_key == dedupe)):
        return False
    safe = {k: v for k, v in (metadata or {}).items() if k in {"device_family", "browser_family", "error_class"} and isinstance(v, (str, int, float, bool))}
    db.add(BotTrafficEvent(bot_visit_id=visit.id, bot_id=visit.bot_id, destination_id=visit.destination_id,
                           event_type=event_type, event_time=model_now(),
                           metadata_json=json.dumps(safe, ensure_ascii=False), dedupe_key=dedupe))
    return True


TELEMETRY_EVENTS = {
    "LANDING_RENDERED", "MAX_REDIRECT_ATTEMPTED", "JS_LANDING_STARTED", "PAGE_HIDDEN",
    "PAGE_VISIBLE_AGAIN", "PAGEHIDE", "BEFOREUNLOAD", "FOCUS_LOST", "FOCUS_RETURNED",
    "WEB_FALLBACK_SHOWN", "WEB_FALLBACK_CLICKED", "MINIAPP_JS_STARTED",
    "MAX_WEBAPP_OBJECT_AVAILABLE", "INIT_DATA_PRESENT", "INIT_DATA_VALID",
    "START_PARAM_PRESENT", "START_PARAM_RESOLVED", "IDENTITY_RECEIVED",
    "BIND_REQUEST_SENT", "BIND_SUCCESS", "BIND_FAILED", "CHANNEL_OPEN_REQUESTED",
    "CHANNEL_OPEN_ACCEPTED_BY_BACKEND", "CHANNEL_OPEN_FAILED", "MINIAPP_CHANNEL_HANDOFF_STARTED",
    "MINIAPP_FALLBACK_SHOWN", "MINIAPP_FALLBACK_CLICKED",
    "MINIAPP_PAGE_HIDDEN", "MINIAPP_PAGE_RESTORED", "CLIENT_ERROR",
}


async def _record_safe_telemetry(
    db: AsyncSession,
    visit: TrafficVisit,
    event_type: str,
    *,
    source: str,
    trusted: bool,
    metadata: dict | None = None,
    dedupe_suffix: str | None = None,
) -> bool:
    """Persist bounded telemetry only; never mutate attribution/conversion state."""
    if event_type not in TELEMETRY_EVENTS:
        return False
    # A visit is intentionally bounded so a broken client cannot grow the event
    # table without limit. Existing business events are outside this cap.
    telemetry_count = await db.scalar(select(func.count(TrafficEvent.id)).where(
        TrafficEvent.visit_id == visit.id,
        TrafficEvent.event_type.in_(TELEMETRY_EVENTS),
    )) or 0
    if int(telemetry_count) >= 64:
        return False
    dedupe = f"telemetry:{visit.id}:{event_type}:{dedupe_suffix or ''}"
    if await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
        return False
    safe = {
        "source": source,
        "trusted": bool(trusted),
        "server_timestamp": model_now().isoformat(),
    }
    for key, value in (metadata or {}).items():
        if key in {"client_timestamp", "device_family", "browser_family", "referrer_host", "target_scheme",
                   "target_host", "target_path", "has_startapp", "error_class", "short_message", "stage",
                   "visibility", "event_source", "timing_ms", "signal_type", "status_code", "signals"}:
            if key == "short_message":
                safe[key] = _safe_error_message(value)
            elif key == "signals" and isinstance(value, dict):
                safe[key] = _safe_browser_signals(value)
            elif isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
    db.add(TrafficEvent(
        channel_id=visit.channel_id,
        visit_id=visit.id,
        max_user_id=None,
        event_type=event_type,
        event_time=model_now(),
        metadata_json=json.dumps(safe, ensure_ascii=False, default=str),
        dedupe_key=dedupe,
    ))
    return True


async def _telemetry_visit_from_payload(
    payload: MiniAppTelemetryIn,
    init_data: str | None,
    db: AsyncSession,
) -> tuple[TrafficVisit | None, bool]:
    """Resolve a telemetry visit without treating client data as identity."""
    if init_data:
        try:
            identity = validate_max_init_data(init_data)
            startapp = _signed_startapp(identity)
            if startapp:
                return await db.scalar(select(TrafficVisit).where(TrafficVisit.startapp_token == startapp)), True
        except Exception:
            pass
    if payload.start_param:
        # The raw value is used only for lookup and is never persisted/logged.
        return await db.scalar(select(TrafficVisit).where(TrafficVisit.startapp_token == payload.start_param)), False
    return None, False

@app.get("/health")
async def health():
    return {"status": "ok", "service": "maxibot"}


@app.get("/system/{public_id}", response_class=HTMLResponse)
async def system_landing(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    """System MAXIBOT landing; never resolves to a channel or channel profile."""
    profile = await db.scalar(select(SystemPromotionProfile).where(
        SystemPromotionProfile.system_key == "maxibot",
        SystemPromotionProfile.enabled.is_(True),
        SystemPromotionProfile.public_id == public_id,
    ))
    if not profile:
        raise HTTPException(404, "system landing not found")
    token = secrets.token_urlsafe(24)
    visit = BotTrafficVisit(token=token, bot_id=settings.max_bot_id, destination_id=settings.max_bot_id,
                            yclid=request.query_params.get("yclid"),
                               utm_source=request.query_params.get("utm_source"),
                               utm_medium=request.query_params.get("utm_medium"),
                               utm_campaign=request.query_params.get("utm_campaign"),
                               utm_content=request.query_params.get("utm_content"),
                               utm_term=request.query_params.get("utm_term"),
                               referer=request.headers.get("referer"),
                               user_agent=request.headers.get("user-agent"))
    db.add(visit)
    await db.commit()
    await _bot_telemetry_event(db, visit, "SYSTEM_LANDING_RENDERED", {
        "device_family": _diagnostic_device_family(request),
        "browser_family": _diagnostic_browser_family(request),
    })
    # BOT is an additive destination in the shared Mini App handoff. The
    # existing CHANNEL landing path remains unchanged.
    # BOT reproduces the frozen CHANNEL launch contract without calling the
    # channel route: native MAX scheme on mobile, MAX Web HTTPS on desktop.
    destination = (build_native_miniapp_deep_link if _is_mobile_request(request) else build_web_miniapp_deep_link)(settings.max_bot_username, token)
    await _bot_telemetry_event(db, visit, "SYSTEM_REDIRECT_SENT", {
        "device_family": _diagnostic_device_family(request),
        "browser_family": _diagnostic_browser_family(request),
    })
    await _bot_telemetry_event(db, visit, "MAX_STARTAPP_HANDOFF", {
        "device_family": _diagnostic_device_family(request),
        "browser_family": _diagnostic_browser_family(request),
    })
    await db.commit()
    metrica_counter = valid_counter_id(profile.yandex_counter_id)
    metrica_tag = metrica_tag_html(metrica_counter)
    handoff_url = html.escape(destination, quote=True)
    return HTMLResponse(f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Переход в MAX</title><meta name="description" content="Переход в MAX">
<style>html,body{{margin:0;width:100%;min-height:100%;background:#f3f5f7;font-family:system-ui,-apple-system,sans-serif}}main{{box-sizing:border-box;max-width:460px;margin:18vh auto;padding:28px 24px;text-align:center;background:#fff;border-radius:24px;box-shadow:0 12px 40px #0001}}h1{{margin:0 0 12px;font-size:28px}}p{{margin:0 0 22px;color:#687386;line-height:1.45}}.hint{{font-size:14px}}.primary,.secondary{{display:block;padding:14px 22px;border-radius:14px;text-decoration:none;font-weight:700}}.primary{{background:#1677ff;color:#fff}}.secondary{{margin-top:12px;color:#1677ff;background:#eef4ff}}#fallbackCta{{display:block}}</style>
{metrica_tag}
<main><h1>Переход в MAX</h1><p>Переходим в MAX…</p><a id="fallbackCta" class="primary" href="{handoff_url}">Перейти в MAX</a></main>
<script>
let maxibotHandoffStarted = false;
const mainEl = document.querySelector('main');
function systemSignal(eventType) {{
  try {{ navigator.sendBeacon('/system/traffic/events', new Blob([JSON.stringify({{visitor_token: {json.dumps(token)}, event_type: eventType}})], {{type:'application/json'}})); }} catch (_) {{}}
}}
systemSignal('SYSTEM_REDIRECT_ATTEMPTED');
document.addEventListener('visibilitychange', () => systemSignal(document.visibilityState === 'hidden' ? 'SYSTEM_PAGE_HIDDEN' : 'SYSTEM_PAGE_VISIBLE_AGAIN'), {{passive:true}});
window.addEventListener('pagehide', () => systemSignal('SYSTEM_PAGE_HIDDEN'), {{passive:true}});
const slowTimer = setTimeout(() => mainEl.classList.add('visible'), 1600);
const fallbackTimer = setTimeout(() => mainEl.classList.add('visible', 'delayed'), 3200);
function continueToMax() {{
  if (maxibotHandoffStarted) return;
  maxibotHandoffStarted = true;
  clearTimeout(slowTimer);
  clearTimeout(fallbackTimer);
  window.location.replace({json.dumps(destination)});
}}
if ({json.dumps(bool(metrica_counter))}) {{
  window.addEventListener('maxibot-metrica-error', continueToMax, {{once:true}});
  window.addEventListener('maxibot-metrica-client-id', (event) => {{
    const clientId = String(event?.detail?.clientId || '');
    if (!/^\\d{{5,64}}$/.test(clientId) || !navigator.sendBeacon) return;
    try {{
      navigator.sendBeacon('/system/traffic/metrica/client-id', new Blob([JSON.stringify({{visitor_token: {json.dumps(token)}, client_id: clientId}})], {{type:'application/json'}}));
    }} catch (_) {{}}
  }}, {{once:true}});
  ym({json.dumps(metrica_counter)}, 'hit', window.location.href, {{callback: continueToMax}});
}} else {{
  continueToMax();
}}
</script>''', headers={"Cache-Control": "no-store"})

@app.get("/admin/system/telemetry-report")
async def system_telemetry_report(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    hours: int = Query(default=48, ge=1, le=168),
    db: AsyncSession = Depends(get_session),
):
    admin = await require_admin(x_max_init_data, db)
    if not settings.maxibot_owner_user_id or str(admin.external_id) != settings.maxibot_owner_user_id:
        raise HTTPException(403, "system telemetry is owner-only")
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    visits = list((await db.scalars(select(BotTrafficVisit).where(BotTrafficVisit.created_at >= start))).all())
    visit_ids = [v.id for v in visits]
    events = list((await db.scalars(select(BotTrafficEvent).where(BotTrafficEvent.bot_visit_id.in_(visit_ids)))).all()) if visit_ids else []
    by_visit: dict[str, list[BotTrafficEvent]] = {}
    for event in events:
        by_visit.setdefault(event.bot_visit_id, []).append(event)
    by_device: dict[str, dict[str, int]] = {}
    for visit in visits:
        device = _diagnostic_device_family_from_user_agent(visit.user_agent) if visit.user_agent else "UNKNOWN"
        bucket = by_device.setdefault(device, {name: 0 for name in SYSTEM_TELEMETRY_EVENTS} | {"system_conversion": 0})
        for event in by_visit.get(visit.id, []):
            bucket[event.event_type] = bucket.get(event.event_type, 0) + 1
        if visit.status == "BOT_STARTED":
            bucket["system_conversion"] += 1
    return {"scope": {"hours": hours}, "counts": {"visits": len(visits), "unique_yclid": len({v.yclid for v in visits if v.yclid}), "system_conversions": sum(1 for v in visits if v.status == "BOT_STARTED")}, "by_device": by_device}

@app.post("/system/traffic/events")
async def system_traffic_event(payload: BrowserTrafficEventIn, request: Request, db: AsyncSession = Depends(get_session)):
    if payload.event_type not in SYSTEM_TELEMETRY_EVENTS - {"SYSTEM_LANDING_RENDERED", "SYSTEM_REDIRECT_SENT", "SYSTEM_BOT_STARTED"}:
        raise HTTPException(400, "unsupported system traffic event")
    visit = await db.scalar(select(BotTrafficVisit).where(BotTrafficVisit.token == payload.visitor_token))
    if not visit:
        return {"ok": True, "recorded": False}
    changed = await _bot_telemetry_event(db, visit, payload.event_type, {
        "device_family": _diagnostic_device_family(request),
        "browser_family": _diagnostic_browser_family(request),
    })
    await db.commit()
    return {"ok": True, "recorded": changed}


@app.post("/system/traffic/metrica/client-id")
async def system_metrica_client_id(payload: MetricaClientIdIn, db: AsyncSession = Depends(get_session)):
    """Persist a browser-confirmed ClientID for one system visit only."""
    client_id = payload.client_id.strip()
    if not re.fullmatch(r"\d{5,64}", client_id):
        raise HTTPException(400, "invalid metrica client id")
    visit = await db.scalar(select(BotTrafficVisit).where(BotTrafficVisit.token == payload.visitor_token))
    if not visit:
        raise HTTPException(404, "system traffic visit not found")
    if visit.metrica_client_id and visit.metrica_client_id != client_id:
        raise HTTPException(409, "metrica client id already bound")
    visit.metrica_client_id = client_id
    await db.commit()
    return {"ok": True}


@app.get("/health/live")
async def liveness():
    return {"status": "alive", "service": "maxibot", "release_id": settings.maxibot_release_id}


@app.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_session)):
    try:
        await db.execute(select(1))
    except Exception:
        raise HTTPException(503, "database is not ready")
    return {"status": "ready", "service": "maxibot", "max_mode": settings.max_client_mode, "release_id": settings.maxibot_release_id, "release": os.getenv("MAXIBOT_RELEASE_MARKER", settings.maxibot_release_id), "commit_sha": settings.maxibot_commit_sha}


@app.get("/health/traffic-ready")
async def traffic_readiness(channel_id: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    """Sanitized preflight for the opt-in bot attribution production path."""
    channel = await db.get(Channel, channel_id) if channel_id else None
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel_id, TrafficProfile.enabled.is_(True))) if channel_id else None
    checks = {
        "oauth_credentials": configured(),
        "callback_https": bool(settings.yandex_oauth_redirect_uri and settings.yandex_oauth_redirect_uri.startswith("https://")),
        "bot_username": bool(settings.max_bot_username.strip()),
        "redis_configured": bool(settings.redis_url.strip()),
        "database": True,
        "channel_found": bool(channel),
        "channel_target": valid_max_target(profile.target_url if profile else None),
        "auto_target_discovery": bool(channel and profile and valid_max_target(profile.target_url)),
        "bot_attribution_enabled": settings.traffic_bot_attribution_enabled,
    }
    ready = all(checks.values())
    response = {"status": "ready" if ready else "blocked", "checks": checks}
    if not checks["auto_target_discovery"]:
        response["blocker"] = "AUTO_TARGET_DISCOVERY_NOT_CONFIRMED_FOR_CHANNEL"
    if not ready:
        raise HTTPException(503, detail=response)
    return response

@app.post("/max/webhook")
async def max_webhook(request: Request):
    if not settings.is_real_max or bot_runtime is None:
        raise HTTPException(503, "real MAX client is disabled")
    if settings.max_webhook_secret and request.headers.get("X-Max-Bot-Api-Secret") != settings.max_webhook_secret:
        raise HTTPException(401, "invalid webhook secret")
    from app.integrations.max_client import MaxUpdate
    payload = await request.json()
    logger = __import__("logging").getLogger(__name__)
    logger.warning(
        "MAX webhook shape kind=%s has_updates=%s has_update=%s",
        type(payload).__name__,
        isinstance(payload, dict) and isinstance(payload.get("updates"), list),
        isinstance(payload, dict) and isinstance(payload.get("update"), dict),
    )
    if isinstance(payload, dict) and isinstance(payload.get("updates"), list):
        items = payload["updates"]
    elif isinstance(payload, dict) and isinstance(payload.get("update"), dict):
        items = [payload["update"]]
    elif isinstance(payload, list):
        items = payload
    else:
        items = [payload]
    for item in items:
        if isinstance(item, dict):
            update = bot_runtime.client._parse_update(item) if isinstance(bot_runtime.client, RealMaxClient) else MaxUpdate(**item)
            await bot_runtime.handle(update)
    return {"ok": True}

@app.get("/mini", response_class=FileResponse)
async def mini(request: Request, db: AsyncSession = Depends(get_session)):
    """Dispatch the configured MAX Mini App entry by launch context.

    MAX uses this one configured URL for both the bot's bottom "Открыть"
    button and ``startapp`` deep links.  A plain launch is the owner CRM;
    an opaque start parameter is the unchanged advertising handoff.
    """
    startapp = request.query_params.get("WebAppStartParam") or request.query_params.get("start_param")
    if not startapp:
        return FileResponse(mini_dir / "index.html", headers={"Cache-Control": "no-store, max-age=0"})

    visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.startapp_token == startapp))
    bot_visit = None if visit else await db.scalar(select(BotTrafficVisit).where(BotTrafficVisit.token == startapp))
    if visit:
        await _traffic_diagnostic_event(
            db,
            visit,
            "MINI_APP_LOADED",
            {"device_family": _diagnostic_device_family(request)},
        )
        await _traffic_diagnostic_event(
            db,
            visit,
            "HANDOFF_CONFIRMED",
            {"device_family": _diagnostic_device_family(request)},
        )
        await db.commit()
    elif bot_visit:
        await _bot_telemetry_event(db, bot_visit, "MINI_APP_LOADED", {
            "device_family": _diagnostic_device_family(request),
            "browser_family": _diagnostic_browser_family(request),
        })
        await db.commit()
    return FileResponse(mini_dir / "traffic.html", headers={"Cache-Control": "no-store, max-age=0"})


# BOT-only lifecycle events are declared after the real /mini route so the
# existing channel contract can distinguish route confirmation from generic
# system telemetry declarations.
SYSTEM_HANDOFF_EVENTS = {
    "MINI_APP_LOADED", "HANDOFF_CONFIRMED", "TOKEN_RESOLVED", "MAX_USER_BOUND",
    "SYSTEM_BOT_HANDOFF_CONFIRMED", "BOT_STARTED_CONVERSION_QUEUED",
}


@app.get("/traffic-test/app", response_class=FileResponse)
async def traffic_test_app():
    return FileResponse(mini_dir / "traffic.html", headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/api/traffic/identify")
async def traffic_identify(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
):
    """Bind a verified MAX WebApp identity to the signed startapp visitor."""
    identity, visit, channel, link = await _traffic_identity(x_max_init_data, db)
    await _traffic_event(db, visit, "max_identity_linked", str(identity["external_id"]), {"source": "verified_init_data"})
    await db.commit()
    return {
        "visitor_id": visit.visit_token,
        "max_user_id": str(identity["external_id"]),
        "channel_id": channel.external_id,
        "channel_title": channel.title,
        "channel_avatar_url": channel.avatar_url,
        "linked_at": link.linked_at,
        "next": "channel_open",
    }


@app.post("/api/traffic/resolve")
async def traffic_resolve(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
):
    """Atomically bind verified MAX identity and resolve its channel target."""
    # Additive BOT branch. The existing CHANNEL branch below is intentionally
    # left intact, including its target and membership attribution semantics.
    identity_probe = validate_max_init_data(x_max_init_data or "")
    system_token = _signed_startapp(identity_probe)
    bot_visit = await db.scalar(select(BotTrafficVisit).where(BotTrafficVisit.token == system_token)) if system_token else None
    if bot_visit:
        profile = await db.scalar(select(SystemPromotionProfile).where(
            SystemPromotionProfile.system_key == "maxibot",
            SystemPromotionProfile.enabled.is_(True),
        ))
        if bot_visit.bot_id != settings.max_bot_id or bot_visit.destination_id != settings.max_bot_id or not profile:
            raise HTTPException(409, "handoff destination mismatch")
        user_id = str(identity_probe["external_id"])
        if bot_visit.max_user_id and bot_visit.max_user_id != user_id:
            raise HTTPException(409, "system visit is already linked to another MAX user")
        system_link = await db.scalar(select(BotMaxIdentityLink).where(
            BotMaxIdentityLink.bot_visit_id == bot_visit.id,
        ))
        if system_link and system_link.max_user_id != user_id:
            raise HTTPException(409, "system identity is already linked to another MAX user")
        if not system_link:
            system_link = BotMaxIdentityLink(
                bot_visit_id=bot_visit.id,
                bot_id=bot_visit.bot_id,
                destination_id=bot_visit.destination_id,
                max_user_id=user_id,
                startapp_token=bot_visit.token,
                init_data_fingerprint=hashlib.sha256(
                    f"max-init:{bot_visit.token}:{user_id}".encode()
                ).hexdigest(),
            )
            db.add(system_link)
        # Mini App resolve proves only the technical handoff.  It is not the
        # MAX bot start event and must not create the business conversion.
        bot_visit.max_user_id = user_id
        bot_visit.status = "HANDOFF_CONFIRMED"
        await _bot_telemetry_event(db, bot_visit, "TOKEN_RESOLVED", {"device_family": "MAX_MINI_APP"})
        await _bot_telemetry_event(db, bot_visit, "MAX_USER_BOUND", {"device_family": "MAX_MINI_APP"})
        await _bot_telemetry_event(db, bot_visit, "HANDOFF_CONFIRMED", {"device_family": "MAX_MINI_APP"})
        await _bot_telemetry_event(db, bot_visit, "SYSTEM_BOT_HANDOFF_CONFIRMED", {"device_family": "MAX_MINI_APP"})
        await db.commit()
        return {
            "ok": True,
            "recorded": True,
            "destination_type": "BOT",
            "target_url": build_bot_deep_link(settings.max_bot_username, bot_visit.token),
            "handoff_confirmed": True,
            "metrica_counter_id": int(profile.yandex_counter_id) if profile.yandex_counter_id and profile.yandex_counter_id.isdigit() else None,
        }
    identity, visit, channel, _ = await _traffic_identity(x_max_init_data, db)
    profile = await db.scalar(
        select(TrafficProfile).where(
            TrafficProfile.channel_id == channel.id,
            TrafficProfile.enabled.is_(True),
        )
    )
    target = profile.target_url if profile and valid_max_target(profile.target_url) else None
    if not target:
        await _record_safe_telemetry(db, visit, "CHANNEL_OPEN_FAILED", source="server", trusted=True,
                                     metadata={"error_class": "target_missing", "stage": "resolve"})
        await db.commit()
        raise HTTPException(503, "closed MAX channel target is not configured")

    user_id = str(identity["external_id"])
    await _traffic_event(db, visit, "max_identity_linked", user_id, {"source": "verified_init_data"})
    await _traffic_diagnostic_event(db, visit, "RESOLVE_OK", {"target_present": True})
    await _record_safe_telemetry(db, visit, "CHANNEL_OPEN_ACCEPTED_BY_BACKEND", source="server", trusted=True,
                                 metadata={"target_scheme": urlsplit(target).scheme, "target_host": urlsplit(target).hostname,
                                           "target_path": urlsplit(target).path})
    # This is a server-side handoff milestone: the validated channel target
    # was returned to the Mini App. It does not mean that MAX displayed the
    # channel or that the user pressed "Join".
    await _traffic_diagnostic_event(db, visit, "channel_open_sent", {
        "target_scheme": urlsplit(target).scheme,
        "target_host": urlsplit(target).hostname,
        "target_path": urlsplit(target).path,
    })
    changed = await _traffic_event(db, visit, "channel_open_clicked", user_id)
    visit.to_channel_at = visit.to_channel_at or model_now()
    await activate_join_intent(
        db, visit=visit, max_user_id=user_id,
        window_hours=profile.attribution_window_hours if profile else settings.traffic_attribution_window_hours,
    )
    await db.commit()
    return {
        "ok": True,
        "recorded": changed,
        "channel_id": channel.external_id,
        "target_url": target,
        # The client-side goal is diagnostic only.  Subscription conversion
        # remains the exact userDidSubscribe path after official user_added.
        "metrica_counter_id": (
            int(profile.yandex_counter_id)
            if profile and profile.yandex_enabled and profile.yandex_counter_id and profile.yandex_counter_id.isdigit()
            else None
        ),
    }


@app.post("/api/traffic/miniapp-event")
async def traffic_miniapp_event(
    payload: MiniAppTrafficEventIn,
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
):
    """Best-effort Mini App lifecycle telemetry; never creates attribution."""
    allowed = {
        "MINIAPP_CHANNEL_HANDOFF_STARTED", "MINIAPP_FALLBACK_SHOWN", "MINIAPP_FALLBACK_CLICKED",
        "MINIAPP_PAGE_HIDDEN", "MINIAPP_PAGE_RESTORED",
        "CHANNEL_OPEN_REQUESTED", "CHANNEL_OPEN_FAILED", "CLIENT_ERROR",
    }
    if payload.event_type not in allowed:
        raise HTTPException(400, "unsupported miniapp traffic event")
    identity, visit, _, _ = await _traffic_identity(x_max_init_data, db)
    changed = await _record_safe_telemetry(
        db, visit, payload.event_type, source="miniapp", trusted=True,
        metadata={"visibility": payload.visibility},
    )
    await db.commit()
    return {"ok": True, "recorded": changed}


@app.post("/api/traffic/miniapp-telemetry")
async def traffic_miniapp_telemetry(
    payload: MiniAppTelemetryIn,
    request: Request,
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
):
    """Best-effort diagnostic ingress, including pre-initData client signals.

    When initData is valid the event is trusted server-side. Before that point
    start_param is lookup-only and the event is explicitly untrusted; neither
    branch can create identity, attribution, membership, or conversion state.
    """
    if payload.event_type not in TELEMETRY_EVENTS:
        raise HTTPException(400, "unsupported miniapp telemetry event")
    visit, trusted = await _telemetry_visit_from_payload(payload, x_max_init_data, db)
    if not visit:
        return {"ok": True, "recorded": False, "reason": "visit_not_resolved"}
    changed = await _record_safe_telemetry(
        db, visit, payload.event_type, source="miniapp", trusted=trusted,
        metadata={
            "visibility": payload.visibility,
            "client_timestamp": payload.client_timestamp,
            "error_class": payload.error_class,
            "short_message": payload.short_message,
            "stage": payload.stage,
            "device_family": _diagnostic_device_family_from_signals(payload.raw_signals, request),
            "browser_family": _diagnostic_browser_family(request),
            "signals": _safe_browser_signals(payload.raw_signals),
        },
        dedupe_suffix=(f"{payload.error_class}:{payload.stage}" if payload.event_type == "CLIENT_ERROR" else None),
    )
    await db.commit()
    return {"ok": True, "recorded": changed, "trusted": trusted}


@app.post("/api/traffic/channel-open")
async def traffic_channel_open(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
):
    identity, visit, channel, _ = await _traffic_identity(x_max_init_data, db)
    changed = await _traffic_event(db, visit, "channel_open_clicked", str(identity["external_id"]))
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id, TrafficProfile.enabled.is_(True)))
    target = profile.target_url if profile and valid_max_target(profile.target_url) else None
    if not target:
        raise HTTPException(503, "closed MAX channel target is not configured")
    await _traffic_diagnostic_event(db, visit, "channel_open_sent", {
        "target_scheme": urlsplit(target).scheme,
        "target_host": urlsplit(target).hostname,
        "target_path": urlsplit(target).path,
    })
    visit.to_channel_at = visit.to_channel_at or model_now()
    await activate_join_intent(
        db, visit=visit, max_user_id=str(identity["external_id"]),
        window_hours=profile.attribution_window_hours if profile else settings.traffic_attribution_window_hours,
    )
    await db.commit()
    return {"ok": True, "recorded": changed, "channel_id": channel.external_id, "target_url": target}


@app.post("/api/traffic/check-subscription")
async def traffic_check_subscription(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
):
    identity, visit, channel, _ = await _traffic_identity(x_max_init_data, db)
    user_id = str(identity["external_id"])
    member = await client.get_member(channel.external_id, user_id)
    subscribed = member is not None
    event_type = "subscription_confirmed" if subscribed else "subscription_check_negative"
    await _traffic_event(db, visit, event_type, user_id, {"membership_api": "GET /chats/{chatId}/members?user_ids"})
    subscription = await db.scalar(select(TrafficSubscription).where(TrafficSubscription.visitor_id == visit.id, TrafficSubscription.channel_id == channel.id))
    if subscribed:
        if not subscription:
            subscription = TrafficSubscription(visitor_id=visit.id, max_user_id=user_id, channel_id=channel.id, status="active", confirmed_at=model_now())
            db.add(subscription)
        else:
            subscription.status = "active"
            subscription.lost_at = None
        visit.converted_at = visit.converted_at or model_now()
    elif subscription and subscription.status == "active":
        subscription.status = "lost"
        subscription.lost_at = model_now()
    await db.commit()
    return {
        "visitor_id": visit.visit_token,
        "max_user_id": user_id,
        "channel_id": channel.external_id,
        "membership": subscribed,
        "subscription_status": subscription.status if subscription else None,
    }


@app.get("/admin/traffic-debug")
async def traffic_debug(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
):
    """Read-only PoC diagnostics; protected by the verified MAX admin identity."""
    admin = await require_admin(x_max_init_data, db)
    visits = (await db.scalars(select(TrafficVisit).join(Channel).where(Channel.admin_id == admin.id).order_by(TrafficVisit.visited_at.desc()).limit(100))).all()
    rows = []
    for visit in visits:
        events = (await db.scalars(select(TrafficEvent).where(TrafficEvent.visit_id == visit.id).order_by(TrafficEvent.event_time))).all()
        subscription = await db.scalar(select(TrafficSubscription).where(TrafficSubscription.visitor_id == visit.id, TrafficSubscription.channel_id == visit.channel_id))
        rows.append({
            "visit_id_present": bool(visit.id),
            "yclid_present": bool(visit.yclid),
            "startapp_present": bool(visit.startapp_token),
            "max_identity_linked": bool(visit.max_user_id),
            "device_family": next((_safe_event_field(e, "device_family") for e in events if e.event_type == "LANDING_RENDERED"), None),
            "diagnostic_stages": [e.event_type for e in events if e.event_type in {
                   "LANDING_RENDERED", "STARTAPP_READY", "REDIRECT_STARTED", "HANDOFF_ATTEMPTED",
                "JS_LANDING_STARTED", "MAX_LINK_ASSIGNED", "MAX_REDIRECT_ATTEMPTED", "PAGE_HIDDEN", "PAGE_VISIBLE_AGAIN", "PAGE_RESTORED",
                "PAGEHIDE", "BEFOREUNLOAD", "FOCUS_LOST", "FOCUS_RETURNED", "WEB_FALLBACK_SHOWN", "WEB_FALLBACK_CLICKED",
                "HANDOFF_CONFIRMED", "MINI_APP_LOADED", "RESOLVE_OK", "channel_open_sent",
                "MINIAPP_CHANNEL_HANDOFF_STARTED", "MINIAPP_PAGE_HIDDEN", "MINIAPP_PAGE_RESTORED",
                "MINIAPP_JS_STARTED", "MAX_WEBAPP_OBJECT_AVAILABLE", "INIT_DATA_PRESENT", "INIT_DATA_VALID",
                "START_PARAM_PRESENT", "START_PARAM_RESOLVED", "IDENTITY_RECEIVED", "BIND_REQUEST_SENT", "BIND_SUCCESS",
                "BIND_FAILED", "CHANNEL_OPEN_REQUESTED", "CHANNEL_OPEN_ACCEPTED_BY_BACKEND", "CHANNEL_OPEN_FAILED", "CLIENT_ERROR",
            }],
            "status": visit.status,
            "channel_open_clicked": any(e.event_type == "channel_open_clicked" for e in events),
            "membership": subscription.status if subscription else None,
            "subscription_confirmed": any(e.event_type == "subscription_confirmed" for e in events),
        })
    return {"visits": rows}


@app.get("/admin/traffic/attributions")
async def traffic_attributions(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    channel_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
):
    """Read-only v1 diagnostics with candidates and conversion eligibility."""
    admin = await require_admin(x_max_init_data, db)
    query = select(TrafficMembership).join(Channel).where(Channel.admin_id == admin.id).order_by(TrafficMembership.joined_at.desc()).limit(200)
    if channel_id:
        query = query.where(TrafficMembership.channel_id == channel_id)
    memberships = (await db.scalars(query)).all()
    rows = []
    for membership in memberships:
        join_signal = await db.scalar(select(TrafficEvent).where(
            TrafficEvent.channel_id == membership.channel_id,
            TrafficEvent.max_user_id == membership.max_user_id,
            TrafficEvent.event_type.in_(["join_request_created", "join_request", "pending_first_seen"]),
            TrafficEvent.event_time <= membership.joined_at,
        ).order_by(TrafficEvent.event_time.desc()))
        join_time = join_signal.event_time if join_signal else membership.joined_at
        candidates = (await db.scalars(select(TrafficAttributionCandidate).where(
            TrafficAttributionCandidate.membership_id == membership.id
        ).order_by(TrafficAttributionCandidate.score.desc()))).all()
        visitor_rows = []
        for candidate in candidates:
            visit = await db.get(TrafficVisit, candidate.visit_id)
            if visit:
                clock = visit.redirect_at or visit.to_channel_at
                open_event = await db.scalar(select(TrafficEvent).where(
                    TrafficEvent.visit_id == visit.id,
                    TrafficEvent.event_type.in_(["MAX_OPEN_LIKELY", "MAX_OPEN_CONFIRMED"]),
                    TrafficEvent.event_time <= join_time,
                ).order_by(TrafficEvent.event_time.asc()))
                open_meta = json.loads(open_event.metadata_json or "{}") if open_event else {}
                visitor_rows.append({
                    "visitor_id": visit.visit_token,
                    "redirect_at": clock.isoformat() if clock else None,
                    "delta_redirect_to_join_seconds": round((join_time - clock).total_seconds(), 3) if clock else None,
                    "open_signal": open_event.event_type if open_event else None,
                    "open_at": open_event.event_time.isoformat() if open_event else None,
                    "open_score": open_meta.get("signal_score"),
                    "delta_open_to_join_seconds": round((join_time - open_event.event_time).total_seconds(), 3) if open_event else None,
                    "score": candidate.score,
                    "yclid_present": bool(visit.yclid),
                    "campaign": visit.utm_campaign,
                })
        conversion = await db.scalar(select(OfflineConversion).where(OfflineConversion.membership_id == membership.id).order_by(OfflineConversion.created_at.desc()))
        rows.append({
            "max_user_id": membership.max_user_id,
            "channel_id": membership.channel_id,
            "event_time": membership.joined_at.isoformat(),
            "join_request_time": join_signal.event_time.isoformat() if join_signal else None,
            "confidence": membership.attribution_status,
            "attribution_reason": membership.attribution_reason,
            "score": membership.attribution_score,
            "selected_visitor_id": membership.attributed_visit_id,
            "candidate_count": len(visitor_rows),
            "candidates": visitor_rows,
            # A temporal HIGH score is diagnostic only.  It is never enough
            # to upload an offline conversion.
            "conversion_eligible": membership.attribution_status == "EXACT",
            "conversion_status": conversion.status if conversion else None,
        })
    scoped_channels = select(Channel.id).where(Channel.admin_id == admin.id)
    metric_specs = {
        "redirects": "MAX_REDIRECT_SENT",
        "MAX_OPEN_LIKELY": "MAX_OPEN_LIKELY",
        "MAX_OPEN_CONFIRMED": "MAX_OPEN_CONFIRMED",
        "join_events": "join_request_created",
    }
    metrics = {}
    for name, event_type in metric_specs.items():
        event_types = ["join_request_created", "join_request", "pending_first_seen"] if name == "join_events" else [event_type]
        metrics[name] = int(await db.scalar(select(func.count(TrafficEvent.id)).where(
            TrafficEvent.channel_id.in_(scoped_channels), TrafficEvent.event_type.in_(event_types)
        )) or 0)
    for status in ("HIGH", "MEDIUM", "AMBIGUOUS", "UNATTRIBUTED"):
        metrics[status] = int(await db.scalar(select(func.count(TrafficMembership.id)).join(Channel).where(
            Channel.admin_id == admin.id, TrafficMembership.attribution_status == status
        )) or 0)
    metrics["high_with_open_signal"] = int(await db.scalar(select(func.count(TrafficMembership.id)).join(Channel).where(
        Channel.admin_id == admin.id, TrafficMembership.attribution_status == "HIGH", TrafficMembership.attributed_visit_id.is_not(None),
        TrafficMembership.attributed_visit_id.in_(select(TrafficEvent.visit_id).where(TrafficEvent.event_type == "MAX_OPEN_LIKELY"))
    )) or 0)
    return {"metrics": metrics, "attributions": rows}


@app.get("/admin/traffic/funnel-report")
async def traffic_funnel_report(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    channel_id: str | None = Query(default=None),
    campaign: str | None = Query(default=None, min_length=1, max_length=255),
    hours: int = Query(default=48, ge=1, le=168),
    db: AsyncSession = Depends(get_session),
):
    """Read-only, owner-scoped comparison of requests and paid yclid clicks.

    This is intentionally aggregate-only.  It distinguishes the canonical
    count of unique campaign yclids from raw landing requests and exposes the
    deepest observed stage for each unique yclid without returning any value
    that could identify a visitor.
    """
    admin = await require_admin(x_max_init_data, db)
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(hours=hours)
    query = (
        select(TrafficVisit)
        .join(Channel)
        .where(Channel.admin_id == admin.id, TrafficVisit.landing_at >= period_start)
        .order_by(TrafficVisit.landing_at.desc())
        .limit(5000)
    )
    if channel_id:
        query = query.where(TrafficVisit.channel_id == channel_id)
    visits = list((await db.scalars(query)).all())
    visit_ids = [visit.id for visit in visits]
    stages_by_visit: dict[str, set[str]] = {visit_id: set() for visit_id in visit_ids}
    if visit_ids:
        events = (await db.execute(select(TrafficEvent.visit_id, TrafficEvent.event_type).where(
            TrafficEvent.visit_id.in_(visit_ids),
            TrafficEvent.event_type.in_({
                "MAX_LINK_ASSIGNED", "MINI_APP_LOADED", "HANDOFF_CONFIRMED", "RESOLVE_OK",
                "max_identity_linked", "channel_open_sent", "MINIAPP_CHANNEL_HANDOFF_STARTED",
            }),
        ))).all()
        for visit_id, event_type in events:
            if visit_id:
                stages_by_visit.setdefault(visit_id, set()).add(event_type)
    report = canonical_paid_click_report(
        (
            FunnelObservation(
                yclid=visit.yclid,
                utm_campaign=visit.utm_campaign,
                stages=frozenset(stages_by_visit.get(visit.id, set())),
            )
            for visit in visits
        ),
        requested_campaign=campaign,
    )
    return {
        "definition": {
            "direct_attributed_unique_yclid": "Unique non-empty yclid values among visits matching the requested campaign. This is the product-side canonical Direct-attributed entry count, not raw landing requests and not an API-reconciled Yandex Direct UI click count.",
            "duplicate_or_revisit_requests": "Additional matching landing requests that reused an already counted yclid.",
        },
        "scope": {
            "hours": hours,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "campaign_filter_present": bool(campaign),
            "channel_filter_present": bool(channel_id),
        },
        "counts": report,
    }


def _yandex_report_number(value: str | None, *, integer: bool = False) -> int | float:
    """Parse a Direct TSV numeric cell without exposing raw report data."""
    text = (value or "").strip().replace(" ", "").replace(",", ".")
    if not text or text in {"—", "-"}:
        return 0
    try:
        return int(float(text)) if integer else float(text)
    except ValueError:
        return 0


@app.get("/admin/traffic/reconciliation-report")
async def traffic_reconciliation_report(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    channel_id: str = Query(..., min_length=1),
    campaign: str = Query(..., min_length=1, max_length=255),
    direct_campaign_id: int = Query(..., gt=0),
    date_from: date_type = Query(...),
    date_to: date_type = Query(...),
    goal_id: int | None = Query(default=None, gt=0),
    db: AsyncSession = Depends(get_session),
):
    """Read-only reconciliation of Yandex Direct and product-side funnel data.

    Direct's report is fetched with the stored channel OAuth token. Product
    counts remain based on the same Moscow calendar dates and campaign suffix
    matching used by the funnel report. No identifiers are returned.
    """
    if date_to < date_from:
        raise HTTPException(422, "date_to must be on or after date_from")
    admin = await require_admin(x_max_init_data, db)
    channel = await get_channel(channel_id, db, admin.id)
    encrypted = await get_encrypted_token(admin.external_id, channel.id, db=db)
    if not encrypted:
        raise HTTPException(409, "Yandex Direct is not connected for this channel")
    try:
        token = decrypt_token(encrypted)
    except Exception:
        raise HTTPException(502, "stored Yandex Direct token is invalid")

    direct_client = YandexDirectClient(token)
    try:
        direct_rows = await direct_client.get_campaign_report(
            direct_campaign_id,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            goal_id=goal_id,
        )
    except (httpx.HTTPError, YandexDirectAPIError) as exc:
        logger.warning("YANDEX_DIRECT_REPORT_FAILED error_type=%s", type(exc).__name__)
        raise HTTPException(502, "Yandex Direct report is temporarily unavailable")
    finally:
        await direct_client.close()

    moscow = ZoneInfo("Europe/Moscow")
    period_start = datetime.combine(date_from, time_type.min, tzinfo=moscow).astimezone(timezone.utc)
    period_end = datetime.combine(date_to + timedelta(days=1), time_type.min, tzinfo=moscow).astimezone(timezone.utc)
    visits = list((await db.scalars(
        select(TrafficVisit).where(
            TrafficVisit.channel_id == channel.id,
            TrafficVisit.landing_at >= period_start,
            TrafficVisit.landing_at < period_end,
            or_(TrafficVisit.utm_campaign == campaign, TrafficVisit.utm_campaign.like(f"{campaign}.%")),
        )
    )).all())
    visit_ids = [visit.id for visit in visits]
    stage_events = []
    if visit_ids:
        stage_events = list((await db.execute(select(TrafficEvent.visit_id, TrafficEvent.event_type).where(
            TrafficEvent.visit_id.in_(visit_ids),
            TrafficEvent.event_type.in_({
                "MAX_LINK_ASSIGNED", "HANDOFF_CONFIRMED", "MINI_APP_LOADED", "RESOLVE_OK",
                "channel_open_sent", "CHANNEL_OPEN_ACCEPTED_BY_BACKEND", "user_added",
            }),
        ))).all())
    stage_counts = {
        stage: len({visit_id for visit_id, event_type in stage_events if event_type == stage})
        for stage in (
            "MAX_LINK_ASSIGNED", "HANDOFF_CONFIRMED", "MINI_APP_LOADED", "RESOLVE_OK",
            "channel_open_sent", "CHANNEL_OPEN_ACCEPTED_BY_BACKEND", "user_added",
        )
    }
    unique_yclids = {visit.yclid for visit in visits if visit.yclid}
    memberships = list((await db.scalars(select(TrafficMembership).where(
        TrafficMembership.channel_id == channel.id,
        TrafficMembership.attributed_visit_id.in_(visit_ids) if visit_ids else False,
    ))).all())
    exact_memberships = sum(1 for membership in memberships if membership.attribution_status == "EXACT")

    def total(field: str, *, integer: bool = False) -> int | float:
        return sum(_yandex_report_number(row.get(field), integer=integer) for row in direct_rows)

    direct = {
        "campaign_id": direct_campaign_id,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "rows": len(direct_rows),
        "impressions": total("Impressions", integer=True),
        "clicks": total("Clicks", integer=True),
        "conversions": total("Conversions"),
        "cost": total("Cost"),
        "goal_id": goal_id,
    }
    product = {
        "campaign_matched_raw_landing_requests": len(visits),
        "direct_attributed_raw_yclid_requests": sum(1 for visit in visits if visit.yclid),
        "direct_attributed_unique_yclid": len(unique_yclids),
        "duplicate_or_revisit_requests": sum(1 for visit in visits if visit.yclid) - len(unique_yclids),
        "campaign_matched_missing_yclid_requests": sum(1 for visit in visits if not visit.yclid),
    }
    return {
        "scope": {"channel_id": channel.id, "campaign": campaign, "date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
        "direct_api": direct,
        "product_funnel": product,
        "max_stages": stage_counts,
        "memberships": {"exact_attributed_memberships": exact_memberships, "attributed_membership_rows": len(memberships)},
        "reconciliation": {
            "direct_clicks_minus_raw_landing_requests": direct["clicks"] - product["campaign_matched_raw_landing_requests"],
            "direct_clicks_minus_unique_yclid": direct["clicks"] - product["direct_attributed_unique_yclid"],
            "direct_conversions_minus_exact_memberships": direct["conversions"] - exact_memberships,
            "note": "Direct clicks/conversions and MAXIBOT visits/memberships are different systems. A zero difference is not required; yclid is the per-visit bridge, while exact membership is the verified downstream conversion.",
        },
    }

def _telemetry_final_status(stages: set[str]) -> str:
    if "RESOLVE_OK" in stages or "CHANNEL_OPEN_ACCEPTED_BY_BACKEND" in stages or "channel_open_sent" in stages:
        return "COMPLETE_TO_CHANNEL_REQUEST"
    if "CHANNEL_OPEN_FAILED" in stages:
        return "CHANNEL_OPEN_REQUEST_FAILED"
    if "IDENTITY_RECEIVED" in stages and "BIND_SUCCESS" not in stages:
        return "IDENTITY_FAILED"
    if "INIT_DATA_PRESENT" in stages and "INIT_DATA_VALID" not in stages:
        return "INIT_DATA_PRESENT_INVALID"
    if "MINIAPP_JS_STARTED" in stages and "INIT_DATA_PRESENT" not in stages:
        return "MINIAPP_STARTED_NO_INIT_DATA"
    if "PAGE_HIDDEN" in stages and "MINIAPP_JS_STARTED" not in stages:
        return "PAGE_HIDDEN_THEN_RETURNED" if "PAGE_VISIBLE_AGAIN" in stages else "REDIRECT_ATTEMPTED_PAGE_HIDDEN_NO_MINIAPP_CONFIRMED"
    if "MAX_REDIRECT_ATTEMPTED" in stages:
        return "REDIRECT_ATTEMPTED_PAGE_STAYED_VISIBLE"
    if "MAX_LINK_ASSIGNED" in stages:
        return "REDIRECT_NOT_ATTEMPTED"
    return "UNKNOWN_NO_TELEMETRY"


@app.get("/admin/traffic/telemetry-report")
async def traffic_telemetry_report(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    channel_id: str | None = Query(default=None),
    campaign: str | None = Query(default=None, min_length=1, max_length=255),
    hours: int = Query(default=48, ge=1, le=168),
    db: AsyncSession = Depends(get_session),
):
    """Owner-scoped aggregate telemetry; no raw identifiers are returned."""
    admin = await require_admin(x_max_init_data, db)
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = select(TrafficVisit).join(Channel).where(
        Channel.admin_id == admin.id, TrafficVisit.landing_at >= start,
    ).order_by(TrafficVisit.landing_at.desc()).limit(5000)
    if channel_id:
        query = query.where(TrafficVisit.channel_id == channel_id)
    if campaign:
        query = query.where(TrafficVisit.utm_campaign == campaign)
    visits = list((await db.scalars(query)).all())
    stage_counts: dict[str, int] = {}
    final_status_counts: dict[str, int] = {}
    device_counts: dict[str, int] = {}
    device_stage_counts: dict[str, dict[str, int]] = {}
    device_final_status_counts: dict[str, dict[str, int]] = {}
    browser_counts: dict[str, int] = {}
    trusted_counts = {"trusted": 0, "untrusted": 0}
    error_counts: dict[str, int] = {}
    timings: dict[str, list[float]] = {}
    unique_yclids = {visit.yclid for visit in visits if visit.yclid}
    for visit in visits:
        events = list((await db.scalars(select(TrafficEvent).where(
            TrafficEvent.visit_id == visit.id,
            TrafficEvent.event_time >= start,
            TrafficEvent.event_type.in_(TELEMETRY_EVENTS | {
                "MAX_LINK_ASSIGNED", "MINI_APP_LOADED", "HANDOFF_CONFIRMED", "RESOLVE_OK", "channel_open_sent",
                # Include outcomes only when MAX already linked them to this
                # exact visit; otherwise they remain unattributed.
                "join_request", "join_request_created", "pending_first_seen", "user_added",
            }),
        ).order_by(TrafficEvent.event_time))).all())
        stages = {event.event_type for event in events}
        for stage in stages:
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        status = _telemetry_final_status(stages)
        final_status_counts[status] = final_status_counts.get(status, 0) + 1
        device = next((_safe_event_field(event, "device_family") for event in events if _safe_event_field(event, "device_family")), "UNKNOWN")
        browser = next((_safe_event_field(event, "browser_family") for event in events if _safe_event_field(event, "browser_family")), "UNKNOWN")
        device_counts[device] = device_counts.get(device, 0) + 1
        per_device_stages = device_stage_counts.setdefault(device, {})
        for stage in stages:
            per_device_stages[stage] = per_device_stages.get(stage, 0) + 1
        per_device_statuses = device_final_status_counts.setdefault(device, {})
        per_device_statuses[status] = per_device_statuses.get(status, 0) + 1
        browser_counts[browser] = browser_counts.get(browser, 0) + 1
        for event in events:
            metadata = {}
            try:
                metadata = json.loads(event.metadata_json or "{}")
            except (TypeError, ValueError):
                metadata = {}
            # Only events emitted by the new telemetry contract carry an
            # explicit trust marker. Legacy business diagnostics must not be
            # misreported as untrusted client telemetry.
            if "trusted" in metadata:
                trusted_counts["trusted" if metadata.get("trusted") is True else "untrusted"] += 1
            if event.event_type == "CLIENT_ERROR":
                error_class = str(metadata.get("error_class") or "unknown")[:64]
                error_counts[error_class] = error_counts.get(error_class, 0) + 1
        event_times = {event.event_type: event.event_time for event in events}
        pairs = {
            "landing_to_redirect_ms": ("LANDING_RENDERED", "MAX_REDIRECT_ATTEMPTED"),
            "redirect_to_page_hidden_ms": ("MAX_REDIRECT_ATTEMPTED", "PAGE_HIDDEN"),
            "redirect_to_miniapp_ms": ("MAX_REDIRECT_ATTEMPTED", "MINIAPP_JS_STARTED"),
            "miniapp_to_identity_ms": ("MINIAPP_JS_STARTED", "IDENTITY_RECEIVED"),
            "identity_to_bind_ms": ("IDENTITY_RECEIVED", "BIND_SUCCESS"),
            "bind_to_channel_request_ms": ("BIND_SUCCESS", "CHANNEL_OPEN_REQUESTED"),
        }
        for name, (left, right) in pairs.items():
            if left in event_times and right in event_times and event_times[right] >= event_times[left]:
                timings.setdefault(name, []).append(round((event_times[right] - event_times[left]).total_seconds() * 1000, 1))

    def percentiles(values: list[float]) -> dict | None:
        if not values:
            return None
        ordered = sorted(values)
        def pick(q: float) -> float:
            return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))]
        return {"count": len(ordered), "avg_ms": round(sum(ordered) / len(ordered), 1), "p50_ms": pick(.50), "p95_ms": pick(.95)}

    return {
        "scope": {"hours": hours, "period_start": start.isoformat(), "campaign_filter_present": bool(campaign), "channel_filter_present": bool(channel_id)},
        "counts": {"raw_visits": len(visits), "unique_yclid": len(unique_yclids), "repeated_yclid_requests": len(visits) - len(unique_yclids)},
        "stage_counts": stage_counts,
        "final_status_counts": final_status_counts,
        "device_family": device_counts,
        "device_stage_counts": device_stage_counts,
        "device_final_status_counts": device_final_status_counts,
        "browser_family": browser_counts,
        "trusted_events": trusted_counts,
        "errors": error_counts,
        "timings": {name: percentiles(values) for name, values in timings.items()},
        "retention_note": "Aggregate only; raw yclid/startapp/initData/MAX user_id/cookies/UA/referrer are not returned.",
    }


@app.get("/api/channels")
async def channels(admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(Channel).options(selectinload(Channel.conversations)).where(Channel.admin_id == admin.id).order_by(Channel.title))).all()
    return [{"id": x.id, "title": x.title, "accepting": x.accepting, "slug": x.public_slug, "avatar_url": x.avatar_url,
             "unread": sum(c.unread for c in x.conversations)} for x in rows]


@app.get("/api/miniapp/owner-state")
async def miniapp_owner_state(admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    """Return the owner Mini App home state, isolated to the signed-in Admin."""
    rows = (await db.scalars(
        select(Channel).options(selectinload(Channel.conversations)).where(
            Channel.admin_id == admin.id,
        ).order_by(Channel.title)
    )).all()
    bots = (await db.scalars(select(BotConnection).where(BotConnection.owner_admin_id == admin.id).order_by(BotConnection.display_name, BotConnection.username))).all()
    username = (settings.max_bot_username or "").lstrip("@").strip()
    return {
        "channels": [
            {
                "id": channel.id,
                "title": channel.title,
                "avatar_url": channel.avatar_url,
                "accepting": channel.accepting,
                "unread": sum(conversation.unread for conversation in channel.conversations),
            }
            for channel in rows
        ],
        "bots": [
            {
                "id": bot.id,
                "title": bot.display_name or (f"@{bot.username.lstrip('@')}" if bot.username else "Бот"),
                "avatar_url": None,
                "status": bot.status,
            }
            for bot in bots
        ],
        "connect_url": f"https://max.ru/{username}" if username else None,
        "connect_instructions": [
            "Откройте настройки вашего канала",
            "Перейдите в раздел «Подписчики»",
            "Нажмите «Добавить подписчиков»",
            "Найдите в поиске «МАКСИБОТ» и добавьте",
            "Перейдите в раздел «Администраторы» и добавьте «МАКСИБОТ» в администраторы канала",
        ],
    }


@app.get("/api/miniapp/analytics/{project_type}/{project_id}")
async def miniapp_project_analytics(
    project_type: str,
    project_id: str,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    """Return the same owner-scoped promotion analytics used by the bot menu."""
    if project_type == "channel":
        project = await db.scalar(select(Channel).where(Channel.id == project_id, Channel.admin_id == admin.id))
        if not project:
            raise HTTPException(404, "project not found")
        channel_ids = [project.id]
        title = project.title
    elif project_type == "bot":
        project = await db.scalar(select(BotConnection).where(BotConnection.id == project_id, BotConnection.owner_admin_id == admin.id))
        if not project:
            raise HTTPException(404, "project not found")
        channel_ids = list((await db.scalars(select(Channel.id).where(
            Channel.admin_id == admin.id,
            Channel.bot_connection_id == project.id,
        ))).all())
        title = project.display_name or (f"@{project.username.lstrip('@')}" if project.username else "Бот")
    else:
        raise HTTPException(400, "unsupported project type")
    return {"project_type": project_type, "title": title, "stats": await traffic_stats_for_channels(db, channel_ids)}

@app.get("/api/dialogs")
async def dialogs(channel_id: str | None = None, status: str = Query("all", pattern="^(all|new|closed)$"), q: str = "", admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    query = select(Conversation).join(Channel).options(selectinload(Conversation.channel), selectinload(Conversation.messages)).where(Channel.admin_id == admin.id).order_by(Conversation.last_message_at.desc())
    if channel_id: query = query.where(Conversation.channel_id == channel_id)
    if status != "all": query = query.where(Conversation.status == status)
    rows = (await db.scalars(query)).all()
    q = q.lower().strip()
    return [{"id": x.id, "user_name": x.user_name, "avatar_url": x.avatar_url, "channel_avatar_url": x.channel.avatar_url, "unread": x.unread, "status": x.status,
             "last_message_at": x.last_message_at.isoformat(), "source": x.channel.title,
             "last_message": x.messages[-1].text if x.messages else ""}
            for x in rows if not q or q in x.user_name.lower() or q in x.channel.title.lower()]

@app.get("/api/dialogs/{conversation_id}")
async def dialog(conversation_id: str, admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    c = await db.scalar(select(Conversation).join(Channel).options(selectinload(Conversation.channel), selectinload(Conversation.messages)).where(Conversation.id == conversation_id, Channel.admin_id == admin.id))
    if not c: raise HTTPException(404, "dialog not found")
    c.unread = 0
    await db.commit()
    return {"id": c.id, "user_name": c.user_name, "avatar_url": c.avatar_url, "channel_avatar_url": c.channel.avatar_url, "source": c.channel.title, "status": c.status,
            "blocked": await db.scalar(select(func.count()).select_from(BlockedUser).where(BlockedUser.channel_id == c.channel_id, BlockedUser.user_external_id == c.user_external_id)) > 0,
            "messages": [{"id": m.id, "direction": m.direction, "text": m.text, "image_url": m.image_url, "created_at": m.created_at.isoformat()} for m in c.messages]}

@app.post("/api/dialogs/{conversation_id}/messages", status_code=201)
async def send_message(conversation_id: str, payload: MessageIn, admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    c = await db.scalar(select(Conversation).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == admin.id))
    if not c: raise HTTPException(404, "dialog not found")
    blocked = await db.scalar(select(func.count()).select_from(BlockedUser).where(BlockedUser.channel_id == c.channel_id, BlockedUser.user_external_id == c.user_external_id))
    if blocked: raise HTTPException(409, "user is blocked")
    message = Message(conversation_id=c.id, direction="out", text=payload.text)
    db.add(message); c.last_message_at = message.created_at
    await db.commit(); await db.refresh(message)
    delivered = await client.send_to_channel_user(c.user_external_id, payload.text)
    if not delivered:
        raise HTTPException(502, "MAX delivery failed")
    return {"id": message.id, "direction": message.direction, "text": message.text, "delivered": True}


@app.post("/api/dialogs/{conversation_id}/image", status_code=201)
async def send_image(
    conversation_id: str,
    request: Request,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    conversation = await db.scalar(select(Conversation).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == admin.id))
    if not conversation:
        raise HTTPException(404, "dialog not found")
    blocked = await db.scalar(select(func.count()).select_from(BlockedUser).where(BlockedUser.channel_id == conversation.channel_id, BlockedUser.user_external_id == conversation.user_external_id))
    if blocked:
        raise HTTPException(409, "user is blocked")
    media_type = (request.headers.get("content-type") or "").split(";", 1)[0].lower()
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/tiff": ".tiff", "image/bmp": ".bmp", "image/heic": ".heic"}.get(media_type)
    if not extension:
        raise HTTPException(415, "supported image formats: JPG, PNG, GIF, TIFF, BMP, HEIC")
    image_data = await request.body()
    if not image_data or len(image_data) > 50 * 1024 * 1024:
        raise HTTPException(413, "image must be between 1 byte and 50 MB")
    image_name = f"{uuid.uuid4().hex}{extension}"
    image_path = uploads_dir / image_name
    image_path.write_bytes(image_data)
    configured_url = settings.max_miniapp_url or settings.max_webhook_url
    if not configured_url:
        image_path.unlink(missing_ok=True)
        raise HTTPException(503, "public application URL is not configured")
    public_parts = urlsplit(configured_url)
    image_url = f"{public_parts.scheme}://{public_parts.netloc}/uploads/{image_name}"
    try:
        await client.send_image_to_channel_user(conversation.user_external_id, image_url)
    except Exception:
        image_path.unlink(missing_ok=True)
        raise HTTPException(502, "MAX image delivery failed")
    message = Message(conversation_id=conversation.id, direction="out", text="Фото", image_url=image_url)
    db.add(message)
    conversation.last_message_at = message.created_at
    await db.commit()
    await db.refresh(message)
    return {"id": message.id, "direction": message.direction, "text": message.text, "image_url": message.image_url, "created_at": message.created_at.isoformat(), "delivered": True}

@app.post("/api/dialogs/{conversation_id}/block")
async def block(conversation_id: str, admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    c = await db.scalar(select(Conversation).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == admin.id))
    if not c: raise HTTPException(404, "dialog not found")
    exists = await db.scalar(select(BlockedUser).where(BlockedUser.channel_id == c.channel_id, BlockedUser.user_external_id == c.user_external_id))
    if not exists: db.add(BlockedUser(channel_id=c.channel_id, user_external_id=c.user_external_id))
    await db.commit(); return {"blocked": True}

@app.post("/api/dialogs/{conversation_id}/unblock")
async def unblock(conversation_id: str, admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    c = await db.scalar(select(Conversation).join(Channel).where(Conversation.id == conversation_id, Channel.admin_id == admin.id))
    if not c: raise HTTPException(404, "dialog not found")
    row = await db.scalar(select(BlockedUser).where(BlockedUser.channel_id == c.channel_id, BlockedUser.user_external_id == c.user_external_id))
    if row: await db.delete(row); await db.commit()
    return {"blocked": False}

@app.patch("/api/channels/{channel_id}/receiving")
async def receiving(channel_id: str, payload: ReceivingIn, admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    c = await get_channel(channel_id, db, admin.id); c.accepting = payload.accepting; await db.commit(); return {"accepting": c.accepting}

@app.get("/api/channels/{channel_id}/templates")
async def templates(channel_id: str, admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    c = await get_channel(channel_id, db, admin.id)
    return {"welcome": normalize_channel_welcome(c.welcome_text), "sent": normalize_channel_sent(c.sent_text)}

@app.patch("/api/channels/{channel_id}/templates/{template_type}")
async def update_template(channel_id: str, template_type: str, payload: TemplateIn, admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    if template_type not in ("welcome", "sent"): raise HTTPException(422, "template type must be welcome or sent")
    c = await get_channel(channel_id, db, admin.id)
    if template_type == "welcome": c.welcome_text = editable_welcome(payload.text)
    else: c.sent_text = payload.text
    await db.commit(); return {template_type: payload.text}

@app.post("/api/public/start")
async def public_start(channel: str, db: AsyncSession = Depends(get_session)):
    c = await db.scalar(select(Channel).where(Channel.public_slug == channel))
    if not c: raise HTTPException(410, "Канал недоступен.")
    return {"channel": c.title, "text": render_channel_welcome_message(c.welcome_text)}

@app.post("/api/public/messages", status_code=201)
async def public_message(channel: str, user_id: str, user_name: str, payload: MessageIn, db: AsyncSession = Depends(get_session)):
    c = await db.scalar(select(Channel).where(Channel.public_slug == channel))
    if not c: raise HTTPException(410, "Канал недоступен.")
    if not c.accepting: raise HTTPException(409, "receiving is disabled")
    blocked = await db.scalar(select(func.count()).select_from(BlockedUser).where(BlockedUser.channel_id == c.id, BlockedUser.user_external_id == user_id))
    if blocked: raise HTTPException(409, "user is blocked")
    conv = await db.scalar(select(Conversation).where(Conversation.channel_id == c.id, Conversation.user_external_id == user_id))
    if not conv: conv = Conversation(channel_id=c.id, user_external_id=user_id, user_name=user_name); db.add(conv); await db.flush()
    conv.unread += 1
    msg = Message(conversation_id=conv.id, direction="in", text=payload.text); db.add(msg)
    await db.commit(); return {"conversation_id": conv.id, "text": normalize_channel_sent(c.sent_text)}


@app.get("/oauth/yandex/start")
async def yandex_oauth_start(admin_id: str, channel_id: str, db: AsyncSession = Depends(get_session)):
    if not configured():
        raise HTTPException(503, "Yandex Metrica OAuth is not configured")
    admin = await db.scalar(select(Admin).where(Admin.external_id == admin_id))
    channel = await db.scalar(select(Channel).where(Channel.id == channel_id, Channel.admin_id == admin.id if admin else False))
    if not admin or not channel:
        raise HTTPException(404, "channel not found")
    return RedirectResponse(authorize_url(await create_state(admin.external_id, channel.id)), status_code=302)


@app.get("/oauth/yandex/direct/start")
async def yandex_direct_oauth_start(admin_id: str, channel_id: str, db: AsyncSession = Depends(get_session)):
    """Start the combined OAuth flow required before a Direct API read."""
    if not configured() or "direct:api" not in (settings.yandex_oauth_scope or "").split():
        raise HTTPException(503, "Yandex Direct API OAuth is not configured")
    admin = await db.scalar(select(Admin).where(Admin.external_id == admin_id))
    channel = await db.scalar(select(Channel).where(Channel.id == channel_id, Channel.admin_id == admin.id if admin else False))
    if not admin or not channel:
        raise HTTPException(404, "channel not found")
    state = await create_state(admin.external_id, channel.id)
    return RedirectResponse(authorize_url(state, scopes=combined_scope()), status_code=302)


@app.get("/oauth/yandex/system/start")
async def yandex_system_oauth_start(admin_id: str, db: AsyncSession = Depends(get_session)):
    if not configured():
        raise HTTPException(503, "Yandex Metrica OAuth is not configured")
    admin = await db.scalar(select(Admin).where(Admin.external_id == admin_id))
    if not admin or str(admin.external_id) != str(settings.maxibot_owner_user_id or ""):
        raise HTTPException(404, "system promotion not found")
    state = await create_system_state(admin.external_id)
    return RedirectResponse(authorize_url(state), status_code=302)


@app.get("/oauth/yandex/callback", response_class=HTMLResponse)
async def yandex_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None, db: AsyncSession = Depends(get_session)):
    flow = await consume_state(state or "")
    if not flow:
        return HTMLResponse("<h1>Ссылка авторизации устарела</h1>", status_code=400)
    if error or not code:
        return HTMLResponse("<h1>Подключение отменено</h1>", status_code=400)
    stage = "exchange"
    try:
        payload = await exchange_code(code)
        if flow.get("system") == "maxibot":
            encrypted = await save_system_token(flow["admin"], payload, db=db)
            stage = "system_metrica_setup"
            metrica_setup = await ensure_system_setup(encrypted)
            profile = await db.scalar(select(SystemPromotionProfile).where(SystemPromotionProfile.system_key == "maxibot"))
            if not profile:
                raise ValueError("system promotion profile unavailable")
            profile.yandex_counter_id = str(metrica_setup["counter_id"])
            profile.yandex_goal_name = str(metrica_setup["goal_name"])
            profile.yandex_owner_login = str(metrica_setup["owner_login"]) if metrica_setup.get("owner_login") else None
            await db.commit()
            return HTMLResponse("<h1>Яндекс Метрика MAXIBOT подключена</h1><p>Системный счётчик и цель bot_started настроены.</p>")
        channel = await db.get(Channel, flow["channel"])
        if not channel:
            raise ValueError("channel unavailable")
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == flow["channel"]))
        if not profile:
            profile = TrafficProfile(channel_id=flow["channel"], yandex_enabled=True, attribution_window_hours=settings.traffic_attribution_window_hours, conversion_policy=settings.traffic_conversion_policy)
            db.add(profile)
            await db.flush()
        else:
            profile.yandex_enabled = True
        encrypted = await save_token(flow["admin"], flow["channel"], payload, db=db)
        stage = "metrica_setup"
        metrica_setup = await ensure_channel_setup(flow["admin"], flow["channel"], encrypted, channel.title)
        profile.yandex_counter_id = str(metrica_setup["counter_id"])
        await db.commit()
    except Exception as exc:
        response = getattr(exc, "response", None)
        logger.warning(
            "YANDEX_OAUTH_CALLBACK_FAILED stage=%s error_type=%s http_status=%s",
            stage,
            type(exc).__name__,
            getattr(response, "status_code", None),
        )
        return HTMLResponse("<h1>Не удалось подключить Яндекс Метрику</h1><p>Проверьте права приложения и повторите попытку.</p>", status_code=502)
    return HTMLResponse("<h1>Яндекс Метрика подключена</h1><p>Счётчик и цели привязаны к выбранному каналу.</p>")


async def _landing_page(public_id: str, request: Request, db: AsyncSession):
    landing = await db.scalar(select(LandingPage).where(LandingPage.public_id == public_id, LandingPage.is_active.is_(True)))
    if not landing:
        raise HTTPException(404, "landing not found")
    channel = await db.get(Channel, landing.channel_id)
    if not channel:
        raise HTTPException(404, "channel not found")
    params = request.query_params
    token = request.cookies.get("maxibot_traffic_visit")
    visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.visit_token == token, TrafficVisit.landing_page_id == landing.id)) if token else None
    incoming_yclid = params.get("yclid")
    # MAX Web/iOS can fetch the landing more than once without retaining the
    # cookie.  A yclid is the ad-click identity, so reuse its one recent
    # unbound visit instead of minting several competing bot payloads.
    if visit and incoming_yclid and visit.yclid != incoming_yclid:
        visit = None
    if not visit and incoming_yclid:
        visit = await db.scalar(
            select(TrafficVisit)
            .where(
                TrafficVisit.landing_page_id == landing.id,
                TrafficVisit.yclid == incoming_yclid,
                TrafficVisit.max_user_id.is_(None),
                TrafficVisit.landing_at >= model_now() - timedelta(minutes=15),
            )
            .order_by(TrafficVisit.landing_at.desc())
            .limit(1)
        )
    # The browser cookie is intentionally long-lived for UTM/yclid continuity,
    # while the opaque MAX bot token is short-lived.  Never reuse an expired
    # unbound bot token on a new landing open: create a fresh visitor record
    # and issue a new token instead.  Reusing it made a current landing URL
    # route to MAX with an already-expired start payload.
    if (
        visit
        and settings.traffic_bot_attribution_enabled
        and visit.startapp_token
        and not visit.max_user_id
        and visit.landing_at
        and (model_now() - visit.landing_at).total_seconds() > settings.traffic_startapp_ttl_seconds
    ):
        visit.status = "EXPIRED"
        visit = None
    visit_created = False
    if not visit:
        token = secrets.token_urlsafe(18)
        visit = TrafficVisit(
            visit_token=token,
            # Only an opaque server-side reference is carried by the optional
            # bot deep link; visitor data and credentials are never embedded.
            startapp_token=new_attribution_token() if settings.traffic_bot_attribution_enabled else None,
            landing_page_id=landing.id,
            channel_id=channel.id,
            utm_source=params.get("utm_source"),
            utm_medium=params.get("utm_medium"),
            utm_campaign=params.get("utm_campaign"),
            utm_content=params.get("utm_content"),
            utm_term=params.get("utm_term"),
            yclid=params.get("yclid"),
            metrica_client_id=params.get("_ym_uid") or params.get("ym_client_id"),
            referer=request.headers.get("referer"),
            user_agent=request.headers.get("user-agent"),
            landing_at=model_now(),
            status="LANDING_VISIT",
            campaign_metadata_json=json.dumps({
                "path": request.url.path,
                "campaign_id": params.get("campaign_id"),
                "ad_id": params.get("ad_id"),
                "ad_group_id": params.get("ad_group_id"),
            }, ensure_ascii=False),
        )
        db.add(visit)
        await db.commit()
        visit_created = True
    await _traffic_diagnostic_event(
        db,
        visit,
        "LANDING_RENDERED",
        {
            "device_family": _diagnostic_device_family(request),
            "referrer_host": _diagnostic_referrer_host(request),
            "utm_source_present": bool(visit.utm_source),
            "utm_medium_present": bool(visit.utm_medium),
            "yclid_present": bool(visit.yclid),
            "visit_created": visit_created,
        },
    )
    if visit.startapp_token:
        await _traffic_diagnostic_event(
            db,
            visit,
            "STARTAPP_READY",
            {"token_present": True, "device_family": _diagnostic_device_family(request)},
        )
    await db.commit()
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == landing.channel_id, TrafficProfile.enabled.is_(True)))
    target = profile.target_url if profile else None
    target_is_valid = valid_max_target(target)
    if not target_is_valid:
        response = HTMLResponse("""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Переход в MAX</title>
<main style="max-width:420px;margin:18vh auto;padding:28px;text-align:center;font:16px system-ui;background:#fff;border-radius:20px;box-shadow:0 10px 40px #0001"><h1>Переход в MAX</h1><p>Ссылка закрытого MAX-канала пока не настроена.</p></main>""", status_code=503)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.set_cookie("maxibot_traffic_visit", token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax", secure=True)
        return response
    # Approved production path: landing → opaque MAXIBOT tracking deep link.
    # The bot_started event binds MAX user_id to this visit before the
    # channel-scoped invite is delivered. Never use a shared channel link as
    # proof of yclid attribution.
    if settings.traffic_bot_attribution_enabled:
        return await _traffic_redirect(landing.public_id, token, db, via_bot=True, request=request)

    # Direct native fallback is allowed only when attribution is disabled;
    # it must never claim an exact visitor-to-member binding.
    return await _traffic_redirect(landing.public_id, token, db, request=request)

async def _channel_preview(public_id: str, request: Request, db: AsyncSession):
    landing = await db.scalar(select(LandingPage).where(LandingPage.public_id == public_id, LandingPage.is_active.is_(True)))
    if not landing:
        raise HTTPException(404, "landing not found")
    channel = await db.get(Channel, landing.channel_id)
    if not channel:
        raise HTTPException(404, "channel not found")
    token = request.cookies.get("maxibot_traffic_visit")
    visit = await db.scalar(select(TrafficVisit).where(
        TrafficVisit.visit_token == token,
        TrafficVisit.landing_page_id == landing.id,
    )) if token else None
    if not visit:
        raise HTTPException(400, "tracking visit is missing")
    profile = await db.scalar(select(TrafficProfile).where(
        TrafficProfile.channel_id == landing.channel_id,
        TrafficProfile.enabled.is_(True),
    ))
    target = profile.target_url if profile else None
    if not valid_max_target(target):
        raise HTTPException(503, "closed MAX channel target is not configured")
    title = html.escape(channel.title)
    # Native invite paths are opaque and must never be presented as a fake
    # public username.  A public handle can be added later from verified chat
    # metadata; the title is always safe and channel-scoped.
    handle_html = ""
    avatar = html.escape(channel.avatar_url, quote=True) if channel.avatar_url else ""
    avatar_html = f'<img class="avatar" src="{avatar}" alt="">' if avatar else '<div class="avatar fallback">•••</div>'
    preview_click = f"/track/{landing.public_id}/click"
    response = HTMLResponse(f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#66aaf0;color:#101828;font:16px system-ui,-apple-system,sans-serif}}
.card{{width:min(310px,calc(100vw - 48px));padding:38px 26px 34px;text-align:center;background:#fff;border-radius:28px;box-shadow:0 16px 40px #15569a30}}
.avatar{{width:64px;height:64px;object-fit:cover;border-radius:18px;margin-bottom:20px}}.fallback{{display:grid;place-items:center;margin:0 auto 20px;background:#1677ff;color:#fff;font-size:24px;letter-spacing:3px}}
h1{{font-size:24px;line-height:1.2;margin:0 0 6px}}.handle{{color:#667085;margin-bottom:30px}}
a{{display:block;text-decoration:none}}.primary{{padding:14px 20px;border-radius:14px;background:#1677ff;color:#fff;font-weight:700}}.secondary{{margin-top:22px;color:#1677ff;font-weight:600}}
</style></head><body><main class="card">{avatar_html}<h1>{title}</h1>{handle_html}
<a class="primary" href="{preview_click}">Перейти в канал</a>
<a class="secondary" href="{preview_click}">Открыть в браузере</a>
</main></body></html>""")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@app.post("/traffic/events/browser")
async def traffic_browser_event(
    payload: BrowserTrafficEventIn,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Accept best-effort browser telemetry; never call it MAX confirmed."""
    allowed = {
        "LANDING_VISIBLE", "REDIRECT_STARTED", "MAX_REDIRECT_ATTEMPTED", "PAGE_HIDDEN", "PAGE_VISIBLE_AGAIN",
        "PAGEHIDE", "BEFOREUNLOAD", "FOCUS_LOST", "FOCUS_RETURNED", "WINDOW_BLUR",
        "WEB_FALLBACK_SHOWN", "WEB_FALLBACK_CLICKED", "CLIENT_ERROR",
        # Browser-side stages distinguish generated HTML from JavaScript that
        # actually ran, without waiting before the MAX handoff.
        "JS_LANDING_STARTED", "MAX_LINK_ASSIGNED", "METRICA_TAG_READY",
        "METRICA_HIT_CONFIRMED", "METRICA_TAG_ERROR", "METRICA_CLIENT_ID_CAPTURED",
    }
    if payload.event_type not in allowed:
        raise HTTPException(400, "unsupported browser traffic event")
    visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.visit_token == payload.visitor_token))
    if not visit:
        raise HTTPException(404, "traffic visitor not found")
    telemetry_count = await db.scalar(select(func.count(TrafficEvent.id)).where(
        TrafficEvent.visit_id == visit.id,
        TrafficEvent.event_type.in_(TELEMETRY_EVENTS),
    )) or 0
    if int(telemetry_count) >= 64:
        return {"ok": True, "recorded": False, "reason": "telemetry_rate_limited", "max_open_likely": False, "max_open_confirmed": False}
    server_time = model_now()
    if payload.event_type == "REDIRECT_STARTED":
        visit.to_channel_at = visit.to_channel_at or server_time
    stage_events = {
        "JS_LANDING_STARTED", "MAX_LINK_ASSIGNED", "MAX_REDIRECT_ATTEMPTED", "PAGE_HIDDEN",
        "PAGE_VISIBLE_AGAIN", "PAGEHIDE", "BEFOREUNLOAD", "FOCUS_LOST", "FOCUS_RETURNED",
        "WEB_FALLBACK_SHOWN", "WEB_FALLBACK_CLICKED", "METRICA_TAG_READY",
        "METRICA_HIT_CONFIRMED", "METRICA_TAG_ERROR", "METRICA_CLIENT_ID_CAPTURED",
    }
    dedupe = (
        f"browser-stage:{visit.id}:{payload.event_type}"
        if payload.event_type in stage_events
        else f"browser-error:{visit.id}:{payload.event_type}:{payload.error_class or 'unknown'}:{payload.stage or 'unknown'}"
        if payload.event_type == "CLIENT_ERROR"
        else f"browser:{visit.id}:{payload.event_type}:{payload.client_timestamp or server_time.isoformat()}"
    )
    if not await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
        db.add(TrafficEvent(
            channel_id=visit.channel_id, visit_id=visit.id,
            event_type=payload.event_type, event_time=server_time,
            dedupe_key=dedupe,
            metadata_json=json.dumps({
                "source": "browser",
                "trusted": False,
                "device_family": _diagnostic_device_family(request),
                "browser_family": _diagnostic_browser_family(request),
                "client_timestamp": payload.client_timestamp,
                "error_class": payload.error_class,
                "short_message": _safe_error_message(payload.short_message),
                "stage": payload.stage,
                "signals": _safe_browser_signals(payload.raw_signals),
                "server_received_at": server_time.isoformat(),
            }, ensure_ascii=False, default=str),
        ))
    likely_created = False
    within_redirect_window = bool(
        visit.redirect_at and
        0 <= (server_time - visit.redirect_at).total_seconds() <= settings.max_open_signal_window_seconds
    )
    if payload.event_type == "PAGE_HIDDEN" and within_redirect_window and settings.max_open_signal_enabled and settings.max_open_likely_enabled:
        likely_dedupe = f"max-open-likely:{visit.id}"
        if not await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == likely_dedupe)):
            db.add(TrafficEvent(
                channel_id=visit.channel_id, visit_id=visit.id,
                event_type="MAX_OPEN_LIKELY", event_time=server_time,
                dedupe_key=likely_dedupe,
                metadata_json=json.dumps({
                    "signal_type": "page_hidden_after_redirect",
                    "source": "browser",
                    "trusted": False,
                    "signal_score": settings.max_open_likely_score,
                    "signals": _safe_browser_signals(payload.raw_signals),
                }, ensure_ascii=False, default=str),
            ))
            likely_created = True
    await db.commit()
    return {"ok": True, "recorded": True, "max_open_likely": likely_created, "max_open_confirmed": False}


@app.post("/traffic/metrica/client-id")
async def traffic_metrica_client_id(
    payload: MetricaClientIdIn,
    db: AsyncSession = Depends(get_session),
):
    """Persist a browser-confirmed ClientID without emitting it in telemetry."""
    client_id = payload.client_id.strip()
    if not re.fullmatch(r"\d{5,64}", client_id):
        raise HTTPException(400, "invalid metrica client id")
    visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.visit_token == payload.visitor_token))
    if not visit:
        raise HTTPException(404, "traffic visitor not found")
    if visit.metrica_client_id and visit.metrica_client_id != client_id:
        raise HTTPException(409, "metrica client id already bound")
    visit.metrica_client_id = client_id
    dedupe = f"metrica-client-id:{visit.id}"
    if not await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
        db.add(TrafficEvent(
            channel_id=visit.channel_id,
            visit_id=visit.id,
            event_type="METRICA_CLIENT_ID_CAPTURED",
            event_time=model_now(),
            dedupe_key=dedupe,
            metadata_json=json.dumps({"source": "metrica", "trusted": False}),
        ))
    await db.commit()
    return {"ok": True}


@app.get("/traffic-test")
async def traffic_test(request: Request, landing: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    """Minimal PoC landing; select a landing explicitly when several exist."""
    if landing:
        page = await db.scalar(select(LandingPage).where(LandingPage.public_id == landing, LandingPage.is_active.is_(True)))
    else:
        pages = (await db.scalars(select(LandingPage).where(LandingPage.is_active.is_(True)).limit(2))).all()
        page = pages[0] if len(pages) == 1 else None
    if not page:
        raise HTTPException(400, "traffic-test requires ?landing=<public_id> when there is not exactly one active landing")
    return await _landing_page(page.public_id, request, db)


@app.get("/traffic-test/click")
async def traffic_test_click(landing: str, token: str, request: Request, db: AsyncSession = Depends(get_session)):
    # This route intentionally uses the same direct redirect as the normal
    # landing flow: no bot, mini app, extra screen, or startapp parameter.
    return await _traffic_redirect(landing, token, db, request=request)


async def _traffic_redirect(public_id: str, token: str, db: AsyncSession, *, via_bot: bool = False, request: Request | None = None) -> HTMLResponse | RedirectResponse:
    landing = await db.scalar(select(LandingPage).where(LandingPage.public_id == public_id, LandingPage.is_active.is_(True)))
    visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.visit_token == token, TrafficVisit.landing_page_id == landing.id)) if landing else None
    if not landing or not visit:
        raise HTTPException(404, "landing visit not found")
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == landing.channel_id, TrafficProfile.enabled.is_(True)))
    channel = await db.get(Channel, landing.channel_id)
    target = profile.target_url if profile else None
    if not target:
        raise HTTPException(503, "closed MAX channel target is not configured")
    parsed = urlsplit(target)
    if parsed.scheme != "https" or parsed.netloc != "max.ru" or not parsed.path.strip("/"):
        raise HTTPException(503, "closed MAX channel target is not configured")
    if via_bot and settings.traffic_bot_attribution_enabled and (
        not settings.max_miniapp_url or not settings.max_bot_username
    ):
        raise HTTPException(503, "MAX attribution bridge is not configured")
    redirect_at = model_now()
    visit.redirect_at = visit.redirect_at or redirect_at
    visit.to_channel_at = visit.to_channel_at or redirect_at
    visit.status = "BOT_REDIRECT_SENT" if via_bot else "MAX_REDIRECT_SENT"
    dedupe = f"redirect:{visit.id}"
    if not await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
        db.add(TrafficEvent(channel_id=visit.channel_id, visit_id=visit.id, event_type="MAX_REDIRECT_SENT",
                            event_time=redirect_at, dedupe_key=dedupe,
                            metadata_json=json.dumps({
                                "destination": "max_bot" if via_bot else "native_channel",
                                "device_family": _diagnostic_device_family(request),
                                "referrer_host": _diagnostic_referrer_host(request),
                                "handoff_kind": "native_max_scheme" if via_bot and _is_mobile_request(request) else "max_web_https" if via_bot else "channel_https",
                            }, ensure_ascii=False)))
    await _traffic_diagnostic_event(
        db,
        visit,
        "REDIRECT_STARTED",
        {
            "device_family": _diagnostic_device_family(request),
            "handoff_kind": "native_max_scheme" if via_bot and _is_mobile_request(request) else "max_web_https" if via_bot else "channel_https",
        },
    )
    await _traffic_diagnostic_event(
        db,
        visit,
        "HANDOFF_ATTEMPTED",
        {
            "device_family": _diagnostic_device_family(request),
            "handoff_kind": "native_max_scheme" if via_bot and _is_mobile_request(request) else "max_web_https" if via_bot else "channel_https",
        },
    )
    await db.commit()
    destination = target
    if via_bot and settings.traffic_bot_attribution_enabled:
        if not visit.startapp_token:
            visit.startapp_token = new_attribution_token()
            await db.commit()
        # Automatic iOS navigation to the HTTPS MAX profile stays in Safari
        # and renders the public bot card. Use the native MAX protocol for the
        # mobile zero-click handoff, matching the observed competitor runtime.
        # Desktop remains on MAX Web. Both carry the same one-time token.
        link_builder = build_native_miniapp_deep_link if _is_mobile_request(request) else build_web_miniapp_deep_link
        handoff_destination = link_builder(settings.max_bot_username, visit.startapp_token)
        handoff_shape = _safe_target_metadata(handoff_destination)
        channel_title = html.escape(channel.title or "Канал MAX")
        channel_description = html.escape(promotion_landing_description(landing.description))
        handoff_url = html.escape(handoff_destination, quote=True)
        metrica_tag = metrica_tag_html(profile.yandex_counter_id if profile and profile.yandex_enabled else None)
        metrica_counter = valid_counter_id(profile.yandex_counter_id if profile and profile.yandex_enabled else None)
        mobile_hint = ""
        fallback_cta = f'<a id="fallbackCta" class="primary" href="{handoff_url}">Перейти в канал</a>'
        response = HTMLResponse(f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Переход в MAX</title><meta name="description" content="Переход в MAX"><style>html,body{{margin:0;width:100%;min-height:100%;background:#f3f5f7;font-family:system-ui,-apple-system,sans-serif}}main{{box-sizing:border-box;max-width:460px;margin:18vh auto;padding:28px 24px;text-align:center;background:#fff;border-radius:24px;box-shadow:0 12px 40px #0001}}h1{{margin:0 0 12px;font-size:28px}}p{{margin:0 0 22px;color:#687386;line-height:1.45}}.hint{{font-size:14px}}.primary,.secondary{{display:block;padding:14px 22px;border-radius:14px;text-decoration:none;font-weight:700}}.primary{{background:#1677ff;color:#fff}}.secondary{{margin-top:12px;color:#1677ff;background:#eef4ff}}</style>
{metrica_tag}
<main><h1>Переход в MAX</h1><p>{channel_description}</p>{mobile_hint}{fallback_cta}</main>
<script>
const trafficVisitToken = {json.dumps(token)};
const trafficTargetMeta = {json.dumps(handoff_shape, ensure_ascii=False)};
const trafficEventUrl = '/traffic/events/browser';
function trafficSignal(eventType, extra = {{}}) {{
  // Telemetry is deliberately best-effort: it must never block or replace
  // the native handoff if a browser rejects beacons or Blob construction.
  try {{
    if (!navigator.sendBeacon) return;
    const payload = JSON.stringify({{
      visitor_token: trafficVisitToken,
      event_type: eventType,
      client_timestamp: new Date().toISOString(),
      error_class: extra.error_class,
      short_message: extra.short_message,
      stage: extra.stage,
      raw_signals: {{visibility: document.visibilityState, document_hidden: document.hidden, ...trafficTargetMeta, has_startapp: true}}
    }});
    navigator.sendBeacon(trafficEventUrl, new Blob([payload], {{type: 'application/json'}}));
  }} catch (_) {{}}
}}
trafficSignal('JS_LANDING_STARTED');
trafficSignal('MAX_LINK_ASSIGNED');
trafficSignal('WEB_FALLBACK_SHOWN');
window.addEventListener('maxibot-metrica-ready', () => trafficSignal('METRICA_TAG_READY'), {{once: true}});
window.addEventListener('maxibot-metrica-error', () => trafficSignal('METRICA_TAG_ERROR'), {{once: true}});
window.addEventListener('maxibot-metrica-client-id', (event) => {{
  const clientId = String(event?.detail?.clientId || '');
  if (!/^\\d{{5,64}}$/.test(clientId) || !navigator.sendBeacon) return;
  try {{
    navigator.sendBeacon('/traffic/metrica/client-id', new Blob([JSON.stringify({{visitor_token: trafficVisitToken, client_id: clientId}})], {{type: 'application/json'}}));
    trafficSignal('METRICA_CLIENT_ID_CAPTURED');
  }} catch (_) {{}}
}}, {{passive: true}});
document.querySelector('#fallbackCta')?.addEventListener('click', () => trafficSignal('WEB_FALLBACK_CLICKED'));
window.addEventListener('error', (event) => trafficSignal('CLIENT_ERROR', {{error_class: 'window_error', short_message: String(event?.message || 'client error'), stage: 'landing'}}), {{passive: true}});
window.addEventListener('unhandledrejection', (event) => trafficSignal('CLIENT_ERROR', {{error_class: 'unhandled_rejection', short_message: String(event?.reason?.message || 'rejection'), stage: 'landing'}}), {{passive: true}});
document.addEventListener('visibilitychange', () => {{
  trafficSignal(document.visibilityState === 'hidden' ? 'PAGE_HIDDEN' : 'PAGE_VISIBLE_AGAIN');
}}, {{passive: true}});
window.addEventListener('pagehide', () => trafficSignal('PAGEHIDE'), {{passive: true}});
window.addEventListener('beforeunload', () => trafficSignal('BEFOREUNLOAD'), {{passive: true}});
window.addEventListener('blur', () => trafficSignal('FOCUS_LOST'), {{passive: true}});
window.addEventListener('focus', () => trafficSignal('FOCUS_RETURNED'), {{passive: true}});
window.addEventListener('pageshow', (event) => {{
  if (event.persisted) trafficSignal('PAGE_VISIBLE_AGAIN');
}}, {{passive: true}});
let maxHandoffStarted = false;
function continueToMax() {{
  if (maxHandoffStarted) return;
  maxHandoffStarted = true;
  trafficSignal('MAX_REDIRECT_ATTEMPTED');
  window.location.replace({json.dumps(handoff_destination)});
}}
if ({json.dumps(bool(metrica_counter))}) {{
  // The landing is navigated away from immediately. Send the pageview first
  // and continue only after Yandex acknowledges the hit. If tag.js itself is
  // unavailable, the error event preserves the existing MAX handoff.
  window.addEventListener('maxibot-metrica-error', continueToMax, {{once: true}});
  ym({json.dumps(metrica_counter)}, 'hit', window.location.href, {{callback: () => {{ trafficSignal('METRICA_HIT_CONFIRMED'); continueToMax(); }}}});
}} else {{
  continueToMax();
}}
</script>
''', status_code=200)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response
    return RedirectResponse(destination, status_code=307)


@app.get("/go/{public_id}", response_class=HTMLResponse)
async def landing(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    return await _landing_page(public_id, request, db)


@app.get("/track/{public_id}", response_class=HTMLResponse)
async def channel_tracking_preview(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Render the channel card after the landing's automatic redirect."""
    return await _channel_preview(public_id, request, db)


@app.get("/track/{public_id}/click")
async def channel_tracking_preview_click(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Record the CTA and open this landing's native channel target."""
    token = request.cookies.get("maxibot_traffic_visit")
    if not token:
        raise HTTPException(400, "tracking visit is missing")
    return await _traffic_redirect(public_id, token, db, via_bot=settings.traffic_bot_attribution_enabled, request=request)


@app.get("/go/{public_id}/click")
async def landing_click(public_id: str, token: str, request: Request, db: AsyncSession = Depends(get_session)):
    return await _traffic_redirect(public_id, token, db, request=request)

@app.post("/api/demo/menu")
async def demo_menu(admin_id: str = "demo", screen: str = "home", message_id: str | None = None):
    text = "🤖 MAXIBOT\n\nДобро пожаловать!"
    buttons = [[{"text": "📋 Мои каналы", "callback_data": "channels"}], [{"text": "🤖 Мои боты", "callback_data": "bots"}]]
    if message_id:
        try: await client.edit_message(admin_id, message_id, text, buttons); return {"message_id": message_id, "edited": True}
        except Exception: pass
    new_id = await client.send_message(admin_id, text, buttons)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        admin = await db.scalar(select(Admin).where(Admin.external_id == admin_id))
        if not admin: admin = Admin(external_id=admin_id); db.add(admin); await db.flush()
        admin.service_message_id = new_id; await db.commit()
    return {"message_id": new_id, "edited": False}

from app.api.routes import router
app.include_router(router)


@app.get("/video-instruction", response_class=HTMLResponse)
async def video_instruction_redirect() -> HTMLResponse:
    """Open the promotion video without exposing a VK URL to MAX previews."""
    destination = "https://vkvideo.ru/video-218011822_456239044"
    return HTMLResponse(
        "<!doctype html><meta charset=\"utf-8\">"
        "<title>Видеоинструкция МАКСИБОТА</title>"
        f"<script>location.replace({json.dumps(destination)});</script>"
        f"<a href={json.dumps(destination)}>Открыть видеоинструкцию</a>"
    )

@app.get("/__poc/max-deeplink", response_class=HTMLResponse)
async def max_deeplink_poc() -> HTMLResponse:
    """Temporary read-only native-scheme A/B page; no JS or traffic records."""
    return HTMLResponse(
        '<!doctype html><meta charset="utf-8">'
        '<a href="max://max.ru/se13802047_1_bot">OPEN MAX</a>'
    )

# The production traffic host points at the same app, so the public URL is
# intentionally a single opaque path segment: https://go.maxibot.pro/<public_id>.
@app.get("/{public_id}", response_class=HTMLResponse)
async def landing_root(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    system = await db.scalar(select(SystemPromotionProfile).where(
        SystemPromotionProfile.system_key == "maxibot",
        SystemPromotionProfile.public_id == public_id,
        SystemPromotionProfile.enabled.is_(True),
    ))
    if system:
        return await system_landing(public_id, request, db)
    return await _landing_page(public_id, request, db)


@app.get("/{public_id}/click")
async def landing_root_click(public_id: str, token: str, request: Request, db: AsyncSession = Depends(get_session)):
    return await _traffic_redirect(public_id, token, db, request=request)

from app.bot.texts import WELCOME as _WELCOME
assert _WELCOME
