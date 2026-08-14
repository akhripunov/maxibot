import asyncio
import contextlib
import hashlib
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Header
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.db.base import Base
from app.db.session import engine, get_session
from app.db.models import Admin, Channel, Conversation, Message, BlockedUser, LandingPage, TrafficVisit, TrafficProfile, TrafficEvent, MaxIdentityLink, TrafficSubscription, TrafficMembership, TrafficAttributionCandidate, OfflineConversion
from app.domain.services import WELCOME, SENT, editable_welcome, render_channel_welcome_message, normalize_channel_welcome, normalize_channel_sent
from app.integrations.max_client import RealMaxClient, StubMaxClient
from app.bot.runtime import MaxBotRuntime
from app.core.config import settings
from app.security import validate_max_init_data
from app.integrations.yandex_oauth import authorize_url, configured, consume_state, create_state, exchange_code, get_encrypted_token, save_token
from app.integrations.yandex_metrica import ensure_channel_setup
from app.db.models import now as model_now
from app.traffic.deep_link import build_bot_deep_link, new_attribution_token, valid_max_target
import html
import secrets

client = RealMaxClient() if settings.is_real_max else StubMaxClient()
bot_runtime: MaxBotRuntime | None = None
logger = logging.getLogger(__name__)

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
uploads_dir = Path("/app/uploads")
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/miniapp", StaticFiles(directory=mini_dir), name="miniapp")
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
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
    raw_signals: dict = Field(default_factory=dict)


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
    startapp = _signed_startapp(identity)
    if not startapp:
        raise HTTPException(422, "verified MAX initData has no start_param")
    visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.startapp_token == startapp))
    if not visit:
        raise HTTPException(404, "traffic startapp token not found")
    user_id = str(identity["external_id"])
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

@app.get("/health")
async def health():
    return {"status": "ok", "service": "maxibot"}


@app.get("/health/live")
async def liveness():
    return {"status": "alive", "service": "maxibot"}


@app.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_session)):
    try:
        await db.execute(select(1))
    except Exception:
        raise HTTPException(503, "database is not ready")
    return {"status": "ready", "service": "maxibot", "max_mode": settings.max_client_mode}


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
async def mini():
    return FileResponse(mini_dir / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


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
        "linked_at": link.linked_at,
        "next": "channel_open",
    }


@app.post("/api/traffic/channel-open")
async def traffic_channel_open(
    x_max_init_data: str | None = Header(default=None, alias="X-MAX-Init-Data"),
    db: AsyncSession = Depends(get_session),
):
    identity, visit, channel, _ = await _traffic_identity(x_max_init_data, db)
    changed = await _traffic_event(db, visit, "channel_open_clicked", str(identity["external_id"]))
    visit.to_channel_at = visit.to_channel_at or model_now()
    await db.commit()
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == channel.id, TrafficProfile.enabled.is_(True)))
    target = profile.target_url if profile and valid_max_target(profile.target_url) else None
    if not target:
        raise HTTPException(503, "closed MAX channel target is not configured")
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
            "visitor_id": visit.visit_token,
            "yclid": visit.yclid,
            "startapp_token": visit.startapp_token,
            "max_user_id": visit.max_user_id,
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
                    "yclid": visit.yclid,
                    "campaign": visit.utm_campaign,
                })
        conversion = await db.scalar(select(OfflineConversion).where(OfflineConversion.membership_id == membership.id).order_by(OfflineConversion.created_at.desc()))
        rows.append({
            "max_user_id": membership.max_user_id,
            "channel_id": membership.channel_id,
            "event_time": membership.joined_at.isoformat(),
            "join_request_time": join_signal.event_time.isoformat() if join_signal else None,
            "confidence": membership.attribution_status,
            "score": membership.attribution_score,
            "selected_visitor_id": membership.attributed_visit_id,
            "candidate_count": len(visitor_rows),
            "candidates": visitor_rows,
            "conversion_eligible": membership.attribution_status in {"EXACT", "HIGH"},
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

@app.get("/api/channels")
async def channels(admin: Admin = Depends(require_admin), db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(Channel).options(selectinload(Channel.conversations)).where(Channel.admin_id == admin.id).order_by(Channel.title))).all()
    return [{"id": x.id, "title": x.title, "accepting": x.accepting, "slug": x.public_slug, "avatar_url": x.avatar_url,
             "unread": sum(c.unread for c in x.conversations)} for x in rows]

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
        await save_token(flow["admin"], flow["channel"], payload)
        channel = await db.get(Channel, flow["channel"])
        encrypted = await get_encrypted_token(flow["admin"], flow["channel"])
        if not channel or not encrypted:
            raise ValueError("channel/token unavailable")
        stage = "metrica_setup"
        metrica_setup = await ensure_channel_setup(flow["admin"], flow["channel"], encrypted, channel.title)
        profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == flow["channel"]))
        if not profile:
            profile = TrafficProfile(channel_id=flow["channel"], yandex_enabled=True, attribution_window_hours=settings.traffic_attribution_window_hours, conversion_policy=settings.traffic_conversion_policy)
            db.add(profile)
        else:
            profile.yandex_enabled = True
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
    profile = await db.scalar(select(TrafficProfile).where(TrafficProfile.channel_id == landing.channel_id, TrafficProfile.enabled.is_(True)))
    target = profile.target_url if profile else None
    target_is_valid = valid_max_target(target)
    if not target_is_valid:
        response = HTMLResponse("""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Переход в MAX</title>
<main style="max-width:420px;margin:18vh auto;padding:28px;text-align:center;font:16px system-ui;background:#fff;border-radius:20px;box-shadow:0 10px 40px #0001"><h1>Переход в MAX</h1><p>Ссылка закрытого MAX-канала пока не настроена.</p></main>""", status_code=503)
        response.set_cookie("maxibot_traffic_visit", token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax", secure=True)
        return response
    redirect_at = model_now()
    visit.redirect_at = visit.redirect_at or redirect_at
    visit.status = "BOT_REDIRECT_SENT" if settings.traffic_bot_attribution_enabled else "MAX_REDIRECT_SENT"
    redirect_dedupe = f"redirect:{visit.id}"
    if not await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == redirect_dedupe)):
        db.add(TrafficEvent(channel_id=visit.channel_id, visit_id=visit.id, event_type="MAX_REDIRECT_SENT",
                            event_time=redirect_at, dedupe_key=redirect_dedupe,
                            metadata_json=json.dumps({"delivery": "landing_auto_redirect", "destination": "max_bot" if settings.traffic_bot_attribution_enabled else "native_channel"}, ensure_ascii=False)))
    await db.commit()
    destination = target
    if settings.traffic_bot_attribution_enabled:
        if not visit.startapp_token:
            visit.startapp_token = new_attribution_token()
            await db.commit()
        destination = build_bot_deep_link(settings.max_bot_username, visit.startapp_token)
    token_json = json.dumps(token, ensure_ascii=False)
    destination_json = json.dumps(destination, ensure_ascii=False)
    destination_label = "Открываем MAX-бота…" if settings.traffic_bot_attribution_enabled else "Открываем закрытый канал…"
    response = HTMLResponse(f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Переход в MAX</title>
<style>body{{margin:0;background:#f6f8fb;color:#172033;font:16px system-ui,-apple-system,sans-serif}}main{{max-width:420px;margin:18vh auto;padding:30px 26px;text-align:center;background:#fff;border-radius:20px;box-shadow:0 10px 40px #14213d18}}h1{{font-size:25px;margin:0 0 12px}}p{{color:#667085}}small{{display:block;margin-top:22px;color:#98a2b3}}</style></head>
<body><main><h1>Переход в MAX</h1><p>{destination_label}</p><small>MAXIBOT</small></main>
<script>const token={token_json}, target={destination_json};
function send(type, raw={{}}) {{ const body=JSON.stringify({{visitor_token:token,event_type:type,client_timestamp:new Date().toISOString(),raw_signals:raw}}); if(navigator.sendBeacon) navigator.sendBeacon('/traffic/events/browser',new Blob([body],{{type:'application/json'}})); }}
send('LANDING_VISIBLE'); setTimeout(()=>{{send('REDIRECT_STARTED'); location.replace(target)}},700);</script></body></html>""")
    response.set_cookie("maxibot_traffic_visit", token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax", secure=True)
    return response


@app.post("/traffic/events/browser")
async def traffic_browser_event(payload: BrowserTrafficEventIn, db: AsyncSession = Depends(get_session)):
    """Accept best-effort browser telemetry; never call it MAX confirmed."""
    allowed = {"LANDING_VISIBLE", "REDIRECT_STARTED", "PAGE_HIDDEN", "PAGE_RESTORED", "WINDOW_BLUR"}
    if payload.event_type not in allowed:
        raise HTTPException(400, "unsupported browser traffic event")
    visit = await db.scalar(select(TrafficVisit).where(TrafficVisit.visit_token == payload.visitor_token))
    if not visit:
        raise HTTPException(404, "traffic visitor not found")
    server_time = model_now()
    if payload.event_type == "REDIRECT_STARTED":
        visit.to_channel_at = visit.to_channel_at or server_time
    dedupe = f"browser:{visit.id}:{payload.event_type}:{payload.client_timestamp or server_time.isoformat()}"
    if not await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
        db.add(TrafficEvent(
            channel_id=visit.channel_id, visit_id=visit.id,
            event_type=payload.event_type, event_time=server_time,
            dedupe_key=dedupe,
            metadata_json=json.dumps({
                "source": "browser",
                "client_timestamp": payload.client_timestamp,
                "raw_signals": payload.raw_signals,
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
                    "signal_score": settings.max_open_likely_score,
                    "raw_signals": payload.raw_signals,
                }, ensure_ascii=False, default=str),
            ))
            likely_created = True
    await db.commit()
    return {"ok": True, "recorded": True, "max_open_likely": likely_created, "max_open_confirmed": False}


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
async def traffic_test_click(landing: str, token: str, db: AsyncSession = Depends(get_session)):
    # This route intentionally uses the same direct redirect as the normal
    # landing flow: no bot, mini app, extra screen, or startapp parameter.
    return await _traffic_redirect(landing, token, db)


async def _traffic_redirect(public_id: str, token: str, db: AsyncSession) -> RedirectResponse:
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
    redirect_at = model_now()
    visit.redirect_at = visit.redirect_at or redirect_at
    visit.to_channel_at = visit.to_channel_at or redirect_at
    visit.status = "MAX_REDIRECT_SENT"
    dedupe = f"redirect:{visit.id}"
    if not await db.scalar(select(TrafficEvent.id).where(TrafficEvent.dedupe_key == dedupe)):
        db.add(TrafficEvent(channel_id=visit.channel_id, visit_id=visit.id, event_type="MAX_REDIRECT_SENT",
                            event_time=redirect_at, dedupe_key=dedupe,
                            metadata_json=json.dumps({"destination_url": target}, ensure_ascii=False)))
    await db.commit()
    return RedirectResponse(target, status_code=307)


@app.get("/go/{public_id}", response_class=HTMLResponse)
async def landing(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    return await _landing_page(public_id, request, db)


@app.get("/go/{public_id}/click")
async def landing_click(public_id: str, token: str, db: AsyncSession = Depends(get_session)):
    return await _traffic_redirect(public_id, token, db)

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

# The production traffic host points at the same app, so the public URL is
# intentionally a single opaque path segment: https://go.maxibot.jobjin.ru/<public_id>.
@app.get("/{public_id}", response_class=HTMLResponse)
async def landing_root(public_id: str, request: Request, db: AsyncSession = Depends(get_session)):
    return await _landing_page(public_id, request, db)


@app.get("/{public_id}/click")
async def landing_root_click(public_id: str, token: str, db: AsyncSession = Depends(get_session)):
    return await _traffic_redirect(public_id, token, db)

from app.bot.texts import WELCOME as _WELCOME
assert _WELCOME
