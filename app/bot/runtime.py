from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from decimal import Decimal
from urllib.parse import parse_qs, quote, unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot.menu import MenuRepository, is_maxibot_owner, official_bot_added_diagnostics
from app.db.models import Admin, AdsAiOutcome, AdsAiRun, AdsRecommendation, Channel, Conversation, MaxPollingCheckpoint, Message, SubscriptionOrder
from app.domain.services import default_welcome, render_channel_welcome_message, normalize_channel_sent
from app.core.config import settings
from app.daily_reports import send_due_daily_reports
from app.integrations.max_client import MaxClient, MaxMessage, MaxUpdate, RealMaxClient, MaxApiError
from app.security_tokens import decrypt_token, encrypt_token
from app.ads.service import can_access_ads, run_bot_check, run_check, toggle_campaign_link
from app.ads.objectives import set_objective, disable_objective
from app.ads.autopilot import AUTONOMOUS, ADVISORY, active_links, set_budget, set_mode
from app.ads.outcomes import record_implementation_evidence
from app.ads.events import ADS_AI_RUN_EVENTS, parse_ai_run_event
from app.integrations.robokassa import amount_for_months, configured as robokassa_configured
from app.integrations.subscriptions import subscription_status

logger = logging.getLogger(__name__)


def _feed_author(name: str) -> str:
    return f"🔵 {name}"


def _feed_caption(row: Message, channel_title: str | None = None) -> str:
    name = row.conversation.user_name
    author = f"🟢 Вы → {name}" if row.direction == "out" else f"🔵 {name}"
    caption = f"📢 {channel_title}\n{author}" if channel_title else author
    if row.created_at and row.direction == "out":
        caption += f" · {row.created_at.astimezone().strftime('%H:%M')}"
    if row.text:
        caption += f"\n{row.text}"
    return caption


class MaxBotRuntime:
    def __init__(self, client: MaxClient, session_factory):
        self.client = client
        self.repo = MenuRepository(session_factory)
        self.session_factory = session_factory
        self.marker: int | None = None
        self._stop = asyncio.Event()
        self._user_channels: dict[str, str] = {}
        self._recent_update_keys: dict[str, float] = {}
        self._recent_start_keys: dict[str, float] = {}
        self._recent_start_users: dict[str, float] = {}
        self._recent_traffic_routes: dict[str, tuple[float, dict]] = {}
        self._recent_traffic_replays: dict[str, float] = {}
        self._recent_callback_ids: dict[str, float] = {}
        self._message_locks: dict[str, asyncio.Lock] = {}
        self._custom_markers: dict[str, int | None] = {}
        self._custom_clients: dict[str, RealMaxClient] = {}
        self._custom_poll_task: asyncio.Task | None = None
        self._daily_report_task: asyncio.Task | None = None
        self._ads_ai_event_task: asyncio.Task | None = None
        self._ads_ai_seen_events: set[tuple[str, str]] = set()
        self._broadcast_drafts: dict[str, str] = {}
        self._ads_objective_pending: dict[str, tuple[str, str, str | None]] = {}
        self._ads_budget_pending: dict[str, tuple[str, str, str | None]] = {}

    async def _ai_promotion_active(self, user_id: str) -> bool:
        """Return the owner-scoped paid AI entitlement state."""
        async with self.session_factory() as db:
            admin = await db.scalar(select(Admin).where(Admin.external_id == str(user_id)))
            return bool(admin and (is_maxibot_owner(admin) or subscription_status(admin.ai_promotion_expires_at) == "активен"))

    async def stop(self):
        self._stop.set()
        if self._custom_poll_task and not self._custom_poll_task.done():
            self._custom_poll_task.cancel()
            await asyncio.gather(self._custom_poll_task, return_exceptions=True)
        if self._daily_report_task and not self._daily_report_task.done():
            self._daily_report_task.cancel()
            await asyncio.gather(self._daily_report_task, return_exceptions=True)
        if self._ads_ai_event_task and not self._ads_ai_event_task.done():
            self._ads_ai_event_task.cancel()
            await asyncio.gather(self._ads_ai_event_task, return_exceptions=True)
        for client in self._custom_clients.values():
            await client.close()

    async def run_forever(self):
        # This task lives in the same dedicated worker as the only official
        # MAX polling loop.  It never opens another update stream.
        if self._daily_report_task is None or self._daily_report_task.done():
            self._daily_report_task = asyncio.create_task(self._daily_report_loop())
        if self._ads_ai_event_task is None or self._ads_ai_event_task.done():
            self._ads_ai_event_task = asyncio.create_task(self._ads_ai_event_loop())
        self.marker = await self._load_primary_marker()
        updates_failure_streak = 0
        while not self._stop.is_set():
            try:
                updates, next_marker = await self.client.updates(self.marker)
                updates_failure_streak = 0
                for update in updates:
                    try:
                        await self.handle(update)
                    except Exception as exc:
                        self._log_event_error(update, exc)
                # Advance only after every update in this response has gone
                # through the handler, so a parser/runtime failure cannot
                # silently hide the rest of the batch.
                await self._store_primary_marker(next_marker)
                self.marker = next_marker
                # Keep custom-bot long polling independent.  Waiting here
                # would block the main MAXIBOT polling loop for the full
                # long-poll timeout and make /start and admin callbacks feel
                # delayed whenever a custom bot is connected.
                if self._custom_poll_task is None or self._custom_poll_task.done():
                    self._custom_poll_task = asyncio.create_task(self._poll_custom_bots())
            except asyncio.CancelledError:
                raise
            except MaxApiError as exc:
                if exc.status_code >= 500:
                    updates_failure_streak += 1
                    retry_delay = self._primary_updates_retry_delay(updates_failure_streak)
                    logger.warning(
                        "MAX_UPDATES_TRANSIENT_ERROR status=%s retry_delay_s=%.2f streak=%s",
                        exc.status_code,
                        retry_delay,
                        updates_failure_streak,
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                logger.exception("MAX update cycle failed")
                await asyncio.sleep(3)
            except Exception:
                logger.exception("MAX update cycle failed")
                await asyncio.sleep(3)

    @staticmethod
    def _primary_updates_retry_delay(failure_streak: int) -> float:
        """Bound retries for transient MAX /updates failures without a hot loop."""
        return min(0.25 * (2 ** max(0, failure_streak - 1)), 3.0)

    async def _load_primary_marker(self) -> int | None:
        """Load the durable cursor before the first MAX /updates request."""
        async with self.session_factory() as db:
            checkpoint = await db.get(MaxPollingCheckpoint, "primary")
            return int(checkpoint.marker) if checkpoint and checkpoint.marker is not None else None

    async def _store_primary_marker(self, marker: int | None) -> None:
        """Advance the cursor only after this response was fully handled."""
        if marker is None:
            return
        async with self.session_factory() as db:
            checkpoint = await db.get(MaxPollingCheckpoint, "primary")
            if checkpoint is None:
                checkpoint = MaxPollingCheckpoint(source="primary", marker=int(marker))
                db.add(checkpoint)
            else:
                checkpoint.marker = int(marker)
            await db.commit()

    async def _daily_report_loop(self):
        """Catch up safely after a worker restart, then check once a minute."""
        while not self._stop.is_set():
            try:
                delivered = await send_due_daily_reports(self.client, self.session_factory)
                if delivered:
                    logger.info("DAILY_REPORTS_SENT count=%s", delivered)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("DAILY_REPORT_CYCLE_FAILED")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def _ads_ai_event_loop(self):
        """Consume committed terminal AI events in the existing MAX worker."""
        from redis.asyncio import Redis

        while not self._stop.is_set():
            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            try:
                pubsub = redis.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(ADS_AI_RUN_EVENTS)
                async for message in pubsub.listen():
                    if self._stop.is_set():
                        break
                    event = parse_ai_run_event(message.get("data"))
                    if event:
                        await self._handle_ads_ai_event(*event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ADS_AI_EVENT_LISTENER_FAILED")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
            finally:
                await redis.aclose()

    async def _handle_ads_ai_event(self, run_id: str, status: str):
        key = (run_id, status)
        if key in self._ads_ai_seen_events:
            return
        async with self.session_factory() as db:
            run = await db.scalar(select(AdsAiRun).where(AdsAiRun.id == run_id))
            if not run or run.status != status or not run.ui_admin_external_id or not run.ui_message_id:
                logger.info("ADS_AI_UI_UPDATE_SKIPPED run_id=%s reason=missing_origin_or_state", run_id)
                return
            if run.promotion_object_type == "BOT":
                if run.promotion_object_id != "system-maxibot":
                    logger.warning("ADS_AI_UI_UPDATE_SKIPPED run_id=%s reason=unknown_bot_object", run_id)
                    return
                text, keyboard, _, media = await self.repo.render(
                    run.ui_admin_external_id, "ads_bot:system"
                )
            elif run.promotion_object_type != "CHANNEL" or not run.channel_id:
                logger.warning("ADS_AI_UI_UPDATE_SKIPPED run_id=%s reason=unknown_promotion_object", run_id)
                return
            else:
                channel = await db.scalar(
                    select(Channel).join(Admin, Channel.admin_id == Admin.id).where(
                        Channel.id == run.channel_id,
                        Admin.external_id == run.ui_admin_external_id,
                    )
                )
                if not channel:
                    logger.warning("ADS_AI_UI_UPDATE_SKIPPED run_id=%s reason=missing_channel", run_id)
                    return
                text, keyboard, _, media = await self.repo.render(
                    run.ui_admin_external_id, f"ads:{run.channel_id}", run.channel_id
                )
        try:
            await self.client.edit_message(
                run.ui_admin_external_id,
                run.ui_message_id,
                text,
                keyboard,
                media,
                disable_link_preview=True,
            )
        except Exception:
            # The result remains committed and the dashboard will render it
            # on the next open. Never create a second notification here.
            logger.exception("ADS_AI_UI_UPDATE_FAILED run_id=%s status=%s", run_id, status)
            return
        self._ads_ai_seen_events.add(key)
        if len(self._ads_ai_seen_events) > 2048:
            self._ads_ai_seen_events.clear()
        logger.info("ADS_AI_UI_UPDATE_OK run_id=%s status=%s", run_id, status)

    async def handle(self, update: MaxUpdate):
        logger.warning(
            "MAX update type=%s has_chat=%s has_user=%s has_payload=%s has_text=%s message_keys=%s",
            update.update_type,
            bool(update.chat_id),
            bool(update.user_id),
            bool((update.raw or {}).get("payload")),
            bool(update.message and update.message.text),
            sorted((update.message.raw or {}).keys()) if update.message else [],
        )
        if self._is_duplicate_message_update(update):
            logger.info("MAX duplicate update ignored")
            return
        if update.bot_connection_id:
            if update.update_type == "bot_started":
                await self._custom_start(update)
            elif update.update_type == "message_created" and update.message:
                await self._custom_message(update)
            return
        if update.update_type == "message_callback":
            await self._callback(update)
        elif update.update_type == "bot_started":
            await self._start(update)
        elif update.update_type in {"bot_added", "bot_removed", "dialog_removed", "bot_stopped"}:
            await self._bot_added(update)
        elif update.update_type in {"user_added", "user_removed", "join_request_created", "join_request"}:
            await self._traffic_membership(update)
        elif update.update_type == "message_created" and update.message:
            await self._message(update)

    async def _traffic_membership(self, update: MaxUpdate):
        """Persist factual channel membership events when MAX exposes them."""
        raw = update.raw or {}
        target = raw.get("user") or raw.get("member") or raw.get("added_user") or raw.get("removed_user") or raw.get("subscriber")
        if isinstance(target, dict):
            user_id = target.get("user_id") or target.get("id")
        else:
            user_id = target or raw.get("user_id")
        channel_id = raw.get("channel_id") or raw.get("chat_id") or update.chat_id
        if isinstance(channel_id, dict):
            channel_id = channel_id.get("chat_id") or channel_id.get("id")
        event_id = raw.get("update_id") or raw.get("event_id") or raw.get("id") or raw.get("message_id")
        timestamp = raw.get("timestamp") or raw.get("created_at")
        event_time = None
        if isinstance(timestamp, (int, float)):
            # MAX timestamps are commonly milliseconds since epoch.
            from datetime import datetime, timezone
            event_time = datetime.fromtimestamp(timestamp / (1000 if timestamp > 10_000_000_000 else 1), tz=timezone.utc)
        handled = await self.repo.record_traffic_membership(
            str(channel_id) if channel_id else None,
            str(user_id) if user_id else None,
            update.update_type,
            str(event_id) if event_id else None,
            event_time,
            {"update_type": update.update_type},
        )
        logger.warning("TRAFFIC_MEMBERSHIP type=%s channel=%s user=%s handled=%s", update.update_type, bool(channel_id), bool(user_id), handled)

    def _is_duplicate_message_update(self, update: MaxUpdate) -> bool:
        """Make message delivery idempotent when MAX retries an update."""
        if update.update_type != "message_created" or not update.message:
            return False
        message_id = update.message.external_message_id
        if message_id:
            key = f"message:{message_id}"
        else:
            raw = json.dumps(update.raw or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
            key = "raw:" + hashlib.sha256(raw.encode()).hexdigest()
        now = time.monotonic()
        self._recent_update_keys = {k: seen for k, seen in self._recent_update_keys.items() if now - seen < 120}
        if key in self._recent_update_keys:
            return True
        self._recent_update_keys[key] = now
        return False

    def _claim_start(self, user_id: str, payload: str | None) -> bool:
        """Claim one logical MAX start before any async side effect.

        MAX can deliver the same launch as both ``bot_started`` and a
        ``message_created /start`` (and can retry either update).  The
        payload/user pair is the stable logical identity; claiming it before
        DB work and message sending prevents a second menu or traffic CTA.
        """
        key = f"start:{user_id}:{payload or 'home'}"
        now = time.monotonic()
        self._recent_start_keys = {
            item: seen for item, seen in self._recent_start_keys.items()
            if now - seen < 30
        }
        recent_start_users = getattr(self, "_recent_start_users", {})
        self._recent_start_users = {
            item: seen for item, seen in recent_start_users.items()
            if now - seen < 5
        }
        # MAX may describe one tap as bot_started(payload) and then as a
        # message_created(/start) without that payload.  Those events need a
        # short user-level guard in addition to the longer exact-key guard.
        if key in self._recent_start_keys or user_id in self._recent_start_users:
            return False
        self._recent_start_keys[key] = now
        self._recent_start_users[user_id] = now
        return True

    @staticmethod
    def _log_event_error(update: MaxUpdate, exc: Exception):
        raw = update.raw or {}
        event_id = raw.get("update_id") or raw.get("id") or raw.get("timestamp") or "unknown"
        status = getattr(exc, "status_code", None)
        logger.exception("MAX event failed type=%s id=%s status=%s error=%s", update.update_type, event_id, status, type(exc).__name__)

    async def _poll_custom_bots(self):
        if not isinstance(self.client, RealMaxClient):
            return
        for bot in await self.repo.active_custom_bots():
            try:
                custom = self._custom_clients.get(bot.id)
                if not custom:
                    custom = RealMaxClient(decrypt_token(bot.encrypted_token), self.client.base_url)
                    self._custom_clients[bot.id] = custom
                updates, marker = await custom.updates(self._custom_markers.get(bot.id))
                self._custom_markers[bot.id] = marker
                for update in updates:
                    update.bot_connection_id = bot.id
                    await self.handle(update)
            except MaxApiError as exc:
                if exc.status_code == 401:
                    await self.repo.mark_custom_bot_invalid_token(bot.id)
                    self._custom_clients.pop(bot.id, None)
                    self._custom_markers.pop(bot.id, None)
                    logger.warning("CUSTOM_BOT_TOKEN_INVALID bot_connection_id=%s", bot.id)
                    continue
                logger.exception("CUSTOM_BOT_POLL_FAILED bot_connection_id=%s status=%s", bot.id, exc.status_code)
            except Exception:
                logger.exception("CUSTOM_BOT_POLL_FAILED bot_connection_id=%s", bot.id)

    async def _custom_start(self, update: MaxUpdate):
        bot = await self.repo.bot_by_id(update.bot_connection_id)
        if not bot:
            return
        owner = str(update.user_id or self._event_user_id(update) or "")
        client = self._custom_clients.get(bot.id)
        if not client:
            return
        if bot.status == "WAITING_ADMIN_START" and owner == str(bot.owner_max_user_id):
            await self.repo.activate_custom_bot(bot.id, owner, update.chat_id or update.user_id)
            await client.send_message(update.user_id or update.chat_id, "✅ Бот подключён к MAXIBOT.\n\nТеперь сообщения пользователей будут приходить сюда.")
            await self._render(owner, f"custom_bot_active:{bot.id}")
            return
        if bot.status != "ACTIVE":
            return
        target = update.user_id or update.chat_id
        if target:
            # Every personal channel link carries its channel public_slug in
            # the MAX start payload. Resolve that slug inside this custom bot
            # too, so every linked channel gets its own welcome text rather
            # than a generic bot greeting.
            start_payloads = self._start_payload_candidates(update)
            channel_code = start_payloads[0] if start_payloads else None
            channels = await self.repo.active_custom_channels(bot.id)
            channel = next((item for item in channels if item.public_slug == channel_code), None)
            if channel:
                welcome = render_channel_welcome_message(channel.welcome_text)
            elif channels:
                welcome = "📩 Пришлите текст, фото или видео — сообщение будет передано администратору."
            else:
                welcome = bot.welcome_text or default_welcome()
            await client.send_message(target, welcome)

    @staticmethod
    def _requires_custom_bot_start(exc: Exception) -> bool:
        """Return true only for the MAX error meaning the owner must start the bot."""
        detail = str(getattr(exc, "detail", exc)).lower()
        markers = (
            "start the bot", "start a conversation", "user has not started",
            "conversation has not been started", "cannot send message before start",
            "recipient has not interacted", "chat not found",
        )
        return any(marker in detail for marker in markers)

    async def _auto_activate_custom_bot(self, bot, token: str, owner_id: str) -> str:
        """Try owner delivery immediately; return ACTIVE, START_REQUIRED, or ERROR."""
        logger.warning("CUSTOM_BOT_VALIDATED bot_connection_id=%s", bot.id)
        logger.warning("CUSTOM_BOT_OWNER_DELIVERY_TEST_STARTED bot_connection_id=%s", bot.id)
        custom = RealMaxClient(token, self.client.base_url)
        try:
            await custom.send_message(owner_id, "✅ Бот подключён к MAXIBOT.\n\nТеперь сообщения пользователей будут приходить сюда.")
        except Exception as exc:
            if self._requires_custom_bot_start(exc):
                logger.warning("CUSTOM_BOT_OWNER_START_REQUIRED bot_connection_id=%s", bot.id)
                await custom.close()
                return "START_REQUIRED"
            logger.warning("CUSTOM_BOT_ACTIVATION_FAILED bot_connection_id=%s error_type=%s", bot.id, type(exc).__name__)
            await custom.close()
            return "ERROR"
        await self.repo.activate_custom_bot(bot.id, owner_id, owner_id)
        self._custom_clients[bot.id] = custom
        self._custom_markers[bot.id] = None
        logger.warning("CUSTOM_BOT_OWNER_DELIVERY_TEST_SUCCESS bot_connection_id=%s", bot.id)
        logger.warning("CUSTOM_BOT_ADMIN_CHAT_DISCOVERED bot_connection_id=%s", bot.id)
        logger.warning("CUSTOM_BOT_AUTO_ACTIVATED bot_connection_id=%s", bot.id)
        return "ACTIVE"

    async def _custom_message(self, update: MaxUpdate):
        bot = await self.repo.bot_by_id(update.bot_connection_id)
        message = update.message
        if not bot or bot.status != "ACTIVE" or not message:
            return
        if self._is_start_command((message.text or "").strip().lower()):
            await self._custom_start(update)
            return
        client = self._custom_clients.get(bot.id)
        if not client:
            return
        reply_id = self._reply_link_id(message.raw or {})
        if reply_id and update.chat_id and str(update.chat_id) == str(bot.admin_private_chat_id):
            mapping = await self.repo.custom_mapping_for_reply(bot.id, update.chat_id, reply_id)
            if mapping and (message.text or message.attachments):
                attachments = message.attachments or []
                target = mapping.source_private_chat_id or mapping.source_user_id
                await client.send_feed_message(target, f"📢 Ответ администратора:\n\n{message.text or ''}", attachments)
                await self.repo.save_custom_outbound(bot.id, mapping, message.external_message_id, message.text or "", attachments)
                logger.warning("CUSTOM_REPLY_ROUTED bot_connection_id=%s source_user_id=%s", bot.id, mapping.source_user_id)
            return
        user_id = str(update.user_id or message.sender_id)
        if not (message.text or message.attachments):
            return
        bound_channels = await self.repo.active_custom_channels(bot.id)
        if len(bound_channels) != 1:
            logger.warning(
                "CUSTOM_INBOUND_BLOCKED_NO_UNAMBIGUOUS_CHANNEL bot_connection_id=%s bound_channel_count=%s",
                bot.id, len(bound_channels),
            )
            await client.send_message(
                update.user_id or update.chat_id,
                "⚠️ Канал ещё не настроен для приёма обращений.\nПопробуйте позже.",
            )
            return
        source_chat_id = str(update.chat_id or user_id)
        stored_id, is_new = await self.repo.create_custom_inbound(
            bot.id, user_id, source_chat_id, message.external_message_id or None,
            message.text or "", message.attachments or [],
        )
        if not is_new:
            return
        if not bot.admin_private_chat_id:
            return
        caption = f"📩 Новое сообщение\n\n👤 {update.user_name or message.sender_name}\n\n{message.text or ''}".rstrip()
        feed_id = await client.send_feed_message(bot.admin_private_chat_id, caption, message.attachments or [])
        await self.repo.save_feed_mapping(
            feed_id, stored_id, message.external_message_id or None, user_id, None,
            f"custom:{bot.id}", source_private_chat_id=source_chat_id,
            source_bot_connection_id=bot.id, destination_admin_chat_id=bot.admin_private_chat_id,
        )
        await client.send_message(update.user_id or update.chat_id, bot.sent_text or "✅ Сообщение отправлено.\nОжидайте ответ.")
        logger.warning("CUSTOM_INBOUND_DELIVERED bot_connection_id=%s source_user_id=%s", bot.id, user_id)

    async def _start(self, update: MaxUpdate):
        # POST /messages expects the user_id for a bot dialog. chat_id is only
        # a fallback for update variants that omit the user object.
        user_id = update.user_id or update.chat_id
        start_payloads = self._start_payload_candidates(update)
        channel_code = start_payloads[0] if start_payloads else None
        if not user_id or str(user_id) == "0":
            logger.warning("MAX start ignored: no target user")
            return
        # Recipient-state tracking was introduced after older repository
        # adapters.  Keep /start available while a compatible adapter rolls
        # out; production repository implements this method.
        mark_recipient_active = getattr(self.repo, "mark_bot_recipient_active", None)
        if mark_recipient_active:
            await mark_recipient_active(str(user_id))
        # Claim before the first await. This covers bot_started followed by
        # message_created(/start), retries, and duplicate poller deliveries.
        if not self._claim_start(str(user_id), channel_code):
            logger.info("MAX duplicate start ignored")
            return
        # A PoC traffic startapp is an opaque visitor token, not a channel
        # slug. MAX authenticated the update itself, so bind the user here
        # before rendering any normal bot UI. The link is intentionally kept
        # out of logs and never echoed back as a token.
        if start_payloads and user_id:
            # MAX can carry a stale wrapper payload alongside the current
            # /start value.  A server-side traffic visit match is the only
            # authority, so try all explicitly payload-shaped candidates.
            for traffic_token in start_payloads:
                if await self.repo.bind_system_startapp(traffic_token, str(user_id)):
                    logger.warning("SYSTEM_STARTAPP_BOUND user_present=%s", True)
                    # System promotion must land on the normal MAXIBOT home
                    # menu after the conversion is recorded.  The previous
                    # confirmation-only message left the user in a dead-end
                    # chat panel and made the system landing look broken.
                    await self.repo.reset_navigation(user_id, preserve_service_message=True)
                    self._user_channels.pop(str(user_id), None)
                    await self._render(user_id, "home", force_new=True)
                    logger.warning("SYSTEM_START_MENU_RENDERED")
                    return
                traffic = await self.repo.bind_traffic_startapp(traffic_token, str(user_id), update.user_name)
                if not traffic:
                    traffic = await self.repo.resolve_bound_traffic_token(traffic_token, str(user_id))
                    if traffic:
                        logger.warning("TRAFFIC_STARTAPP_BOUND_TOKEN_ROUTE routing_only=%s", True)
                if not traffic:
                    continue
                if not traffic.get("target_url"):
                    logger.warning("TRAFFIC_STARTAPP_CHANNEL_TARGET_MISSING")
                    return
                logger.warning("TRAFFIC_STARTAPP_BOUND_SILENT channel=%s user_present=%s", bool(traffic.get("channel_id")), bool(user_id))
                return
            # MAX Web on iOS can first deliver the correct bot_started token
            # and then immediately open the native app with an unrelated
            # cached payload. Recover only from this same user's single
            # channel binding made in the last five minutes.
            recovered = await self.repo.recover_recent_traffic_start(str(user_id))
            if recovered:
                logger.warning("TRAFFIC_STARTAPP_IOS_RECOVERED_SILENT single_recent_channel=%s", True)
                return
            # MAX may append another bot_started event with a stale/foreign
            # payload after the valid event was already handled. Never send a
            # contradictory "expired" message on top of a channel CTA that
            # was successfully delivered to the same user moments ago.
            now = time.monotonic()
            self._recent_traffic_routes = {
                key: item for key, item in self._recent_traffic_routes.items()
                if now - item[0] < 300
            }
            recent_route = self._recent_traffic_routes.get(str(user_id))
            if recent_route:
                logger.warning("TRAFFIC_STARTAPP_DUPLICATE_STALE_IGNORED recent_success=%s", True)
                return
            logger.warning(
                "TRAFFIC_STARTAPP_BIND_MISS candidates=%s lengths=%s",
                len(start_payloads),
                [len(value) for value in start_payloads],
            )
        # MAX can represent the same dialog with different fields on a
        # bot_started deep-link event. Prefer the identifier already tied to
        # this user's conversation; this prevents sending the welcome to a
        # non-dialog chat target and reopening stale history without it.
        candidates = [str(value) for value in (update.user_id, update.chat_id) if value not in (None, "", "0")]
        known_target = False
        channel = await self._channel_by_slug(channel_code) if channel_code is not None else None
        if channel:
            channel_user = await self.repo.channel_conversation_user(channel.id, candidates)
            if channel_user:
                user_id = channel_user
                known_target = True
        else:
            for candidate in candidates:
                if await self.repo.has_conversation_user(candidate):
                    user_id = candidate
                    known_target = True
                    break
        logger.warning(
            "MAX start route target=%s payload=%s known_target=%s candidates=%s channel_match=%s",
            bool(user_id), bool(channel_code), known_target, len(candidates), bool(channel),
        )
        if channel_code and channel_code.startswith("traffic_"):
            # Legacy links must not reintroduce the forbidden intermediary
            # MAXIBOT screen. New traffic links go directly to the channel.
            logger.warning("LEGACY_TRAFFIC_DEEPLINK_IGNORED")
            return
        if channel_code is not None:
            logger.warning("MAX deep-link lookup channel_found=%s", bool(channel))
            if channel:
                self._user_channels[user_id] = channel.id
                if await self.repo.get_channel_owner_external(channel.id) == str(user_id):
                    await self.repo.set_self_test(str(user_id), channel.id)
                    logger.warning("MAX channel-user mode enabled channel_id=%s", channel.id)
                # A deep-link visit is an explicit request to open the
                # channel bot.  The conversation may already exist from a
                # previous visit, but the welcome prompt must still be sent.
                # Replayed copies of the same webhook event are filtered by
                # the short start-key guard above.
                await self.repo.ensure_start_conversation(channel.id, user_id, update.user_name, update.user_avatar_url)
                await self.client.send_message(
                    user_id,
                    render_channel_welcome_message(channel.welcome_text),
                    format="markdown",
                    disable_link_preview=True,
                )
                logger.warning("MAX deep-link welcome sent")
                return
            # A payload was supplied, but it no longer maps to a connected channel.
            await self.client.send_message(user_id, "Ссылка на канал устарела. Откройте новую рекламную ссылку.")
            logger.warning("MAX deep-link invalid")
            return
        # A normal /start is a hard HOME reset.
        await self.repo.reset_navigation(user_id, preserve_service_message=True)
        self._user_channels.pop(str(user_id), None)
        # Replace the old service panel with one fresh message at the bottom
        # of the dialog. Editing a panel far above the latest /start leaves
        # obsolete keyboards visible in MAX Web and makes channel lists look
        # inconsistent. _claim_start prevents duplicate replacements when
        # MAX retries the same logical start event.
        first_screen = "home" if await self.repo.onboarding_seen(str(user_id)) else "onboarding"
        message_id = await self._render(user_id, first_screen, force_new=True)
        logger.warning("MAX start menu completed message=%s", bool(message_id))

    @staticmethod
    def _callback_replacement_body(text: str, keyboard: list[list[dict]], media: list[dict] | None) -> dict:
        """Build the documented ``POST /answers`` message replacement body."""
        rows: list[list[dict]] = []
        for row in keyboard:
            converted: list[dict] = []
            for button in row:
                if button.get("callback_data") is not None:
                    converted.append({
                        "type": "callback",
                        "text": button["text"],
                        "payload": button["callback_data"],
                    })
                else:
                    converted.append(dict(button))
            rows.append(converted)
        attachments = list(media or [])
        if rows:
            attachments.append({"type": "inline_keyboard", "payload": {"buttons": rows}})
        body = {"text": text, "attachments": attachments}
        if "**" in text or "[Предложка]" in text or "_Подключение Яндекс.Директ_" in text:
            body["format"] = "markdown"
        elif "<b>" in text or "<strong>" in text:
            body["format"] = "html"
        if "vkvideo.ru" in text:
            body["disable_link_preview"] = True
        return body

    async def _navigation_callback(self, update: MaxUpdate, user_id: str, action: str) -> None:
        """Replace exactly the service message that supplied the callback."""
        if action == "home":
            await self.repo.cancel_free_trial_intent(user_id)
            screen, channel_id = "home", None
        elif action == "channels":
            screen, channel_id = "channels", None
        elif action == "promotion":
            screen, channel_id = "promotion", None
        elif action == "owner_admin":
            screen, channel_id = "owner_admin", None
        elif action == "promotion_maxibot":
            screen, channel_id = "promotion_maxibot", None
        elif action in {"promotion_maxibot_landing", "promotion_maxibot_yandex", "promotion_maxibot_analytics", "promotion_maxibot_settings"}:
            screen, channel_id = action, None
        elif action.startswith("settings:"):
            reference = action.split(":", 1)[1]
            channel_id = await self.repo.resolve_owned_channel_id(user_id, reference)
            screen = f"settings:{channel_id}" if channel_id else "channels"
            channel_id = channel_id if screen != "channels" else None
        elif action.startswith("channel:"):
            reference = action.split(":", 1)[1]
            channel_id = await self.repo.resolve_owned_channel_id(user_id, reference)
            screen = f"channel:{channel_id}" if channel_id else "channels"
            channel_id = channel_id if screen != "channels" else None
        else:
            reference = action.split(":", 1)[1]
            channel_id = await self.repo.resolve_owned_channel_id(user_id, reference)
            screen = f"{action.split(':', 1)[0]}:{channel_id}" if channel_id else "promotion"
            channel_id = channel_id if screen != "promotion" else None

        text, keyboard, _, media = await self.repo.render(user_id, screen, channel_id)
        logger.warning(
            "MAX_CALLBACK_MENU_BUILT screen=%s rows=%s buttons=%s",
            screen,
            len(keyboard),
            sum(len(row) for row in keyboard),
        )

        if screen.startswith("promotion_channel:"):
            logger.warning(
                "ADS_MENU_OUTBOUND_TRACE screen=promotion_channel instance_pid=%s buttons=%s",
                os.getpid(),
                [button.get("text") for row in keyboard for button in row],
            )
        # MAX documents POST /answers with ``message`` as the atomic way to
        # replace the message whose button was pressed.  Do not combine it
        # with DELETE/PUT: MAX Web can then remove the card from the visible
        # timeline even though both HTTP calls return success.
        clicked_mid = update.message.external_message_id if update.message else None
        canonical_mid = await self.repo.service_message_id(user_id)
        if clicked_mid and canonical_mid and clicked_mid != canonical_mid:
            # Continue navigation on the panel the owner actually used,
            # without deleting either bot panel or any user message.
            await self.repo.save_service_message(user_id, clicked_mid)
        if screen.startswith("settings:"):
            # MAX Web currently hides an in-place callback replacement when
            # this particular screen is opened (the API still returns 200).
            # Acknowledge the callback, then create one fresh functional
            # settings panel at the bottom. Do not delete any message.
            await self.client.answer_callback(update.callback_id)
            new_id = await self.client.send_message(user_id, text, keyboard, media)
            await self.repo.save_service_message(user_id, new_id)
            logger.warning("MAX_SETTINGS_PANEL_CREATED message=%s", bool(new_id))
            return
        replacement = self._callback_replacement_body(text, keyboard, media)
        await self.client.answer_callback(update.callback_id, replacement)
        logger.warning(
            "MAX_CALLBACK_MESSAGE_EDITED screen=%s clicked_mid_present=%s",
            screen,
            bool(clicked_mid),
        )

    async def _render_custom_bot_token_background(self, user_id: str) -> None:
        """Keep the original token screen, outside the callback latency path."""
        try:
            await self._render(user_id, "custom_bot_token")
        except Exception:
            logger.exception("CUSTOM_BOT_TOKEN_SCREEN_RENDER_FAILED")

    async def _callback(self, update: MaxUpdate):
        user_id = self._event_user_id(update)
        if not user_id or str(user_id) == "0":
            logger.warning("MAX callback skipped: no valid user id")
            return
        action = update.button_payload or "home"
        logger.warning(
            "MAX_CALLBACK_RECEIVED payload_present=%s payload_kind=%s callback_id_present=%s",
            bool(update.button_payload),
            (update.button_payload or "").split(":", 1)[0] or "<none>",
            bool(update.callback_id),
        )
        if action.startswith(("ads:", "ads_bot")) and not await self._ai_promotion_active(str(user_id)):
            if update.callback_id:
                await self.client.answer_callback(update.callback_id)
            await self._render(user_id, "subscription_ai")
            return
        if action.startswith("ads_campaigns:"):
            selected_id = action.split(":", 1)[1].split(":", 1)[0]
            if selected_id and can_access_ads(str(user_id)) and await self.repo.channel_owned(user_id, selected_id):
                if update.callback_id:
                    await self.client.answer_callback(update.callback_id)
                await self._render(user_id, action, selected_id)
                return
        if action.startswith("ads_campaign_select") and not re.match(
            r"^ads_campaign_select:[0-9a-f-]{20,}:[0-9a-f-]{20,}$", action
        ):
            if update.callback_id:
                await self.client.answer_callback(update.callback_id)
            logger.info("MAX malformed campaign selection callback ignored")
            return
        if action.startswith("ads_campaigns") and not re.match(
            r"^ads_campaigns:[0-9a-f-]{20,}(:\d+)?$", action
        ):
            current_text = update.message.text if update.message else ""
            match = re.search(r"(?:Канал:|📢)\s*(.+?)(?:\n|$)", current_text)
            channel_title = match.group(1).strip() if match else ""
            logger.warning(
                "MAX_CAMPAIGN_RECOVERY text_len=%s title_present=%s channel_id_present=%s",
                len(current_text),
                bool(channel_title),
                bool(update.message and update.message.channel_id),
            )
            if channel_title:
                async with self.session_factory() as db:
                    recovered = await db.scalar(
                        select(Channel).join(Admin).where(
                            Channel.title == channel_title,
                            Admin.external_id == str(user_id),
                        )
                    )
                if recovered and can_access_ads(str(user_id)):
                    if update.callback_id:
                        await self.client.answer_callback(update.callback_id)
                    await self._render(user_id, f"ads_campaigns:{recovered.id}", str(recovered.id))
                    return
        # Older MAX message copies (and a few clients that omit callback
        # arguments) can deliver the read-only creatives action without a
        # usable channel id. Recover the current channel from the visible
        # dashboard instead of falling through to the generic unavailable
        # screen.
        malformed_readonly_ads = {
            "ads_creatives": r"^ads_creatives:[0-9a-f-]{20,}$",
            "ads_learning": r"^ads_learning:[0-9a-f-]{20,}$",
            "ads_history": r"^ads_history:[0-9a-f-]{20,}$",
            "ads_context": r"^ads_context:[0-9a-f-]{20,}$",
        }
        malformed_prefix = next((prefix for prefix in malformed_readonly_ads if action == prefix or action.startswith(prefix + ":")), None)
        if malformed_prefix and not re.match(malformed_readonly_ads[malformed_prefix], action):
            current_text = update.message.text if update.message else ""
            match = re.search(r"(?:Канал:|📢)\s*(.+?)(?:\n|$)", current_text)
            channel_title = match.group(1).strip() if match else ""
            async with self.session_factory() as db:
                recovered = None
                if channel_title:
                    recovered = await db.scalar(
                        select(Channel).join(Admin).where(
                            Channel.title == channel_title,
                            Admin.external_id == str(user_id),
                        )
                    )
                # If MAX supplied a keyboard-only stale copy, use the channel
                # tracked for this dialog as a bounded fallback.
                if not recovered:
                    tracked_id = self._user_channels.get(str(user_id))
                    if tracked_id:
                        recovered = await db.scalar(
                            select(Channel).join(Admin).where(
                                Channel.id == tracked_id,
                                Admin.external_id == str(user_id),
                            )
                        )
            if recovered and can_access_ads(str(user_id)):
                if update.callback_id:
                    await self.client.answer_callback(update.callback_id)
                await self._render(user_id, f"{malformed_prefix}:{recovered.id}", str(recovered.id))
                return
            logger.info("MAX malformed creatives callback ignored")
            return
        if action in {"connect_channel", "about"}:
            await self.repo.mark_onboarding_seen(str(user_id))
        if update.callback_id:
            if update.callback_id in self._recent_callback_ids and not action.startswith("ads_campaigns"):
                logger.info("MAX duplicate callback ignored callback_id=%s", update.callback_id)
                await self.client.answer_callback(update.callback_id)
                return
            self._recent_callback_ids[update.callback_id] = time.monotonic()
        # MAX callback responses can replace exactly the message whose button
        # was pressed.  Prefer this for navigation; editing a separately
        # stored message id leaves an old keyboard visible in some clients.
        navigation = action in {"home", "channels", "promotion", "promotion_maxibot", "promotion_maxibot_landing", "promotion_maxibot_yandex", "promotion_maxibot_analytics", "promotion_maxibot_settings"}
        navigation = navigation or action.startswith(("channel:", "settings:", "promotion_channel:", "promotion_landing:", "subscription:", "ads:"))
        if navigation and update.callback_id:
            await self._navigation_callback(update, str(user_id), action)
            return
        # Non-navigation callbacks keep the immediate acknowledgement to
        # avoid retries while their action performs longer work.
        if update.callback_id and action == "custom_bot_token":
            # MAX can take ~4s to PUT this exact screen.  Acknowledge the tap
            # immediately, then render the unchanged original token screen.
            if await self.repo.set_custom_bot_state(user_id, "waiting_custom_bot_token"):
                await self.client.answer_callback(
                    update.callback_id,
                    notification="Открываю форму подключения…",
                )
                asyncio.create_task(self._render_custom_bot_token_background(user_id))
            return
        if update.callback_id:
            notification = "Обновляем…" if action.startswith(("ads_check:", "ads_bot_check:")) else None
            await self.client.answer_callback(update.callback_id, notification=notification)
        # Old MAX message copies can contain channel/landing IDs that were
        # replaced during earlier menu repairs. Recover to the current list
        # instead of rendering a dead "Канал не найден" screen.
        if action.startswith("channel:"):
            stale_channel_id = action.split(":", 1)[1]
            if not await self.repo.channel_owned(user_id, stale_channel_id):
                await self._render(user_id, "channels")
                return
        if action.startswith("promotion_channel:") or action.startswith("promotion_landing:") or action.startswith("ads:"):
            stale_channel_id = action.split(":", 1)[1]
            if not await self.repo.channel_owned(user_id, stale_channel_id):
                await self._render(user_id, "promotion")
                return
        if action.startswith("promotion_bot"):
            bot_id = action.split(":", 1)[1] if ":" in action else None
            if bot_id and not await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, "promotion")
                return
        if action.startswith("inbox_bind:"):
            await self._bind_inbox_from_callback(update, action.split(":", 1)[1])
            return
        if action.startswith("channel_admin_done:"):
            await self._channel_admin_done(str(user_id), action.split(":", 1)[1])
            return
        if action.startswith("channel_admin_cancel:"):
            await self.client.send_message(str(user_id), "❌ Подключение канала отменено.")
            await self._render(str(user_id), "channels")
            return
        if action.startswith("support_take:") or action.startswith("support_close:"):
            await self._support_status_callback(update, action)
            return
        if action.startswith("traffic_to_channel:"):
            # Kept only so stale legacy callbacks are harmless.
            logger.warning("LEGACY_TRAFFIC_CALLBACK_IGNORED")
            return
        # MAX can deliver a callback from an older copy of the navigation
        # menu.  Keep destructive/service actions protected, but allow the
        # read-only promotion navigation to recover the current service
        # screen; otherwise an old visible menu makes «Лендинг» appear dead.
        callback_message_id = update.message.external_message_id if update.message else ""
        current_message_id = await self.repo.service_message_id(user_id)
        action = update.button_payload or "home"
        navigation_action = action in {"promotion", "connect_channel", "owner_admin", "owner_clients", "ads_storage"} or action.startswith(("promotion_", "subscription", "free_trial", "channels", "channel:", "settings:", "channel_admin_", "ads"))
        if callback_message_id and current_message_id and callback_message_id != current_message_id and not navigation_action:
            logger.info("MAX stale callback ignored")
            return
        screen, channel_id = self._action_parts(action)
        if action == "subscription":
            current_text = update.message.text if update.message else ""
            match = re.search(r"(?:Канал:|📣)\s*(.+?)(?:\n|$)", current_text)
            if match:
                async with self.session_factory() as db:
                    recovered = await db.scalar(select(Channel).join(Admin).where(
                        Channel.title == match.group(1).strip(),
                        Admin.external_id == str(user_id),
                    ))
                if recovered:
                    await self._render(user_id, f"subscription:{recovered.id}", recovered.id)
                    return
            await self._render(user_id, "subscription")
            return
        if screen == "ads_campaigns" and not channel_id:
            # Some MAX clients emit the callback label without the channel
            # id. Recover the channel from the current dashboard/list text.
            current_text = update.message.text if update.message else ""
            match = re.search(r"(?:Канал:|📢)\s*(.+?)(?:\n|$)", current_text)
            channel_title = match.group(1).strip() if match else ""
            if channel_title:
                async with self.session_factory() as db:
                    recovered = await db.scalar(
                        select(Channel).join(Admin).where(
                            Channel.title == channel_title,
                            Admin.external_id == str(user_id),
                        )
                    )
                if recovered and can_access_ads(str(user_id)):
                    await self._render(user_id, f"ads_campaigns:{recovered.id}", str(recovered.id))
                    return
            logger.info("MAX malformed campaign callback ignored")
            return
        if action == "connect_channel":
            await self._render(user_id, "connect_channel")
        elif action == "promotion":
            await self._render(user_id, "promotion")
        elif action == "promotion_connect":
            await self._render(user_id, "promotion_connect")
        elif action == "free_trial":
            await self._render(user_id, "free_trial")
        elif action == "free_trial_start":
            await self.repo.begin_free_trial(str(user_id))
            await self._render(user_id, "connect_channel")
        elif action == "free_trial_connect":
            await self.repo.begin_free_trial(str(user_id))
            await self._render(user_id, "connect_channel")
        elif action.startswith("subscription_tariff:"):
            tariff = action.split(":", 1)[1]
            if tariff in {"start", "pro"}:
                await self._render(user_id, action)
                return
        elif action == "subscription_ai":
            await self._render(user_id, action)
            return
        elif action == "subscription_ai_pay":
            if not robokassa_configured():
                await self._render(user_id, "subscription_ai")
                return
            async with self.session_factory() as db:
                admin = await db.scalar(select(Admin).where(Admin.external_id == str(user_id)))
                if not admin:
                    return
                order = SubscriptionOrder(
                    invoice_id=int(time.time() * 1000), admin_id=admin.id,
                    channel_id=None, months=1, amount=Decimal("1.00"),
                    tariff=None, order_kind="ai_promotion", status="pending",
                )
                db.add(order)
                await db.commit()
                order_id = order.id
            await self._render(user_id, f"subscription_payment:{order_id}")
        elif action.startswith("free_trial_activate:"):
            selected_id = action.split(":", 1)[1]
            if await self.repo.channel_owned(user_id, selected_id):
                await self.repo.activate_free_trial(str(user_id), selected_id)
                await self._render(user_id, "free_trial_start")
        elif action.startswith("promotion_channel:"):
            selected_id = action.split(":", 1)[1].split(":", 1)[0]
            if await self.repo.channel_owned(user_id, selected_id):
                await self._render(user_id, action, selected_id)
        elif action.startswith("subscription:"):
            selected_id = action.split(":", 1)[1]
            if await self.repo.channel_owned(user_id, selected_id):
                await self._render(user_id, action, selected_id)
        elif action.startswith("subscription_plan:"):
            parts = action.split(":")
            selected_id = parts[1]
            tariff = parts[2] if len(parts) == 4 else "start"
            months_text = parts[3] if len(parts) == 4 else parts[2]
            service_subscription = selected_id == "service"
            if service_subscription or await self.repo.channel_owned(user_id, selected_id):
                try:
                    months = int(months_text)
                    amount = amount_for_months(months, tariff)
                except (TypeError, ValueError):
                    return
                if not robokassa_configured():
                    await self._render(user_id, f"subscription:{selected_id}", selected_id)
                    return
                async with self.session_factory() as db:
                    admin = await db.scalar(select(Admin).where(Admin.external_id == str(user_id)))
                    channel = None
                    if not service_subscription:
                        channel = await db.scalar(select(Channel).where(
                            Channel.id == selected_id,
                            Channel.admin_id == admin.id if admin else False,
                        ))
                    if (not service_subscription and not channel) or not admin:
                        return
                    order = SubscriptionOrder(
                        invoice_id=int(time.time() * 1000),
                        admin_id=admin.id,
                        channel_id=channel.id if channel else None,
                        months=months,
                        amount=amount,
                        tariff=tariff if service_subscription else None,
                        status="pending",
                    )
                    db.add(order)
                    await db.commit()
                    order_id = order.id
                await self._render(user_id, f"subscription_payment:{order_id}", None if service_subscription else selected_id)
                return
        elif action.startswith("ads_autopilot_enable:"):
            _, object_type, object_id = action.split(":", 2)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                # A single linked campaign is unambiguous: the primary
                # control toggles it immediately. Multiple links retain the
                # explicit campaign-selection screen.
                async with self.session_factory() as db:
                    links = await active_links(db, object_type=object_type, object_id=object_id)
                    if len(links) == 1:
                        try:
                            await set_mode(db, object_type=object_type, object_id=object_id,
                                           mode=AUTONOMOUS, campaign_id=links[0].campaign_id)
                            await db.commit()
                        except ValueError:
                            await db.rollback()
                        else:
                            await self._render(user_id, f"ads:{object_id}" if object_type == "CHANNEL" else f"ads_bot:{object_id}")
                            return
                await self._render(user_id, action)
                return
        elif action.startswith("ads_autopilot_confirm:"):
            _, object_type, object_id, campaign_id = action.split(":", 3)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                await self._render(user_id, f"ads_autopilot_enable_confirm:{object_type}:{object_id}:{campaign_id}")
                return
        elif action.startswith("ads_autopilot_enable_confirm_apply:"):
            _, object_type, object_id, campaign_id = action.split(":", 3)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                async with self.session_factory() as db:
                    try:
                        await set_mode(db, object_type=object_type, object_id=object_id, mode=AUTONOMOUS, campaign_id=campaign_id)
                        await db.commit()
                    except ValueError:
                        await db.rollback()
                        return
                await self._render(user_id, f"ads:{object_id}" if object_type == "CHANNEL" else f"ads_bot:{object_id}")
                return
        elif action.startswith("ads_autopilot_disable:"):
            _, object_type, object_id = action.split(":", 2)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                async with self.session_factory() as db:
                    links = await active_links(db, object_type=object_type, object_id=object_id)
                    authorized = [link for link in links if str(link.execution_mode or ADVISORY).upper() == AUTONOMOUS]
                    if len(authorized) == 1:
                        await set_mode(db, object_type=object_type, object_id=object_id,
                                       mode=ADVISORY, campaign_id=authorized[0].campaign_id)
                        await db.commit()
                        await self._render(user_id, f"ads:{object_id}" if object_type == "CHANNEL" else f"ads_bot:{object_id}")
                        return
                await self._render(user_id, action)
                return
        elif action.startswith("ads_autopilot_disable_confirm:"):
            _, object_type, object_id, campaign_id = action.split(":", 3)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                async with self.session_factory() as db:
                    try:
                        await set_mode(db, object_type=object_type, object_id=object_id, mode=ADVISORY, campaign_id=campaign_id)
                        await db.commit()
                    except ValueError:
                        await db.rollback()
                        return
                await self._render(user_id, f"ads:{object_id}" if object_type == "CHANNEL" else f"ads_bot:{object_id}")
                return
        elif action.startswith("ads_budget:"):
            _, object_type, object_id = action.split(":", 2)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                await self._render(user_id, action)
                return
        elif action.startswith("ads_budget_input:"):
            route = action.split(":")
            _, object_type, object_id = route[:3]
            campaign_id = route[3] if len(route) > 3 else None
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                self._ads_budget_pending[str(user_id)] = (object_type, object_id, campaign_id)
                await self._render(user_id, action)
                return
        elif action.startswith("ads_check:"):
            selected_id = action.split(":", 1)[1]
            if can_access_ads(str(user_id)) and await self.repo.channel_owned(user_id, selected_id):
                async with self.session_factory() as db:
                    try:
                        origin_message_id = update.message.external_message_id if update.message else None
                        await run_check(
                            db,
                            admin_external_id=str(user_id),
                            channel_id=selected_id,
                            ui_message_id=origin_message_id,
                        )
                    except Exception:
                        logger.exception("ADS_MANUAL_CHECK_FAILED channel_id=%s", selected_id)
                await self._render(user_id, f"ads:{selected_id}", selected_id)
                return
        elif action.startswith("ads_implement:"):
            _, object_type, object_id, recommendation_id = action.split(":", 3)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                if object_type == "CHANNEL" and not await self.repo.channel_owned(user_id, object_id):
                    return
                async with self.session_factory() as db:
                    recommendation = await db.get(AdsRecommendation, recommendation_id)
                    if not recommendation:
                        return
                    if object_type == "CHANNEL":
                        valid_scope = recommendation.promotion_object_type in {None, "CHANNEL"} and recommendation.channel_id == object_id
                    else:
                        valid_scope = recommendation.promotion_object_type == "BOT" and recommendation.promotion_object_id == object_id
                    outcome = await db.scalar(select(AdsAiOutcome).where(
                        AdsAiOutcome.recommendation_id == recommendation.id
                    ).order_by(AdsAiOutcome.due_at.desc())) if valid_scope else None
                    if not outcome:
                        return
                    await record_implementation_evidence(
                        db, outcome,
                        {"evidence_type": "OWNER_CONFIRMATION", "executed_by": "HUMAN", "reference": "main_screen"},
                    )
                    await db.commit()
                await self._render(user_id, f"ads:{object_id}" if object_type == "CHANNEL" else f"ads_bot:{object_id}")
                return
        elif action.startswith("ads_bot_check:"):
            bot_key = action.split(":", 1)[1]
            if can_access_ads(str(user_id)) and bot_key:
                async with self.session_factory() as db:
                    try:
                        origin_message_id = update.message.external_message_id if update.message else None
                        await run_bot_check(db, admin_external_id=str(user_id), bot_key="maxibot", ui_message_id=origin_message_id)
                    except Exception:
                        logger.exception("ADS_MANUAL_BOT_CHECK_FAILED bot_key=%s", bot_key)
                await self._render(user_id, f"ads_bot:{bot_key}")
                return
        elif action.startswith("ads_objective:"):
            _, object_type, object_id = action.split(":", 2)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                if object_type == "CHANNEL" and not await self.repo.channel_owned(user_id, object_id):
                    return
                await self._render(user_id, action)
                return
        elif action.startswith("ads_bot:"):
            bot_key = action.split(":", 1)[1]
            if can_access_ads(str(user_id)) and bot_key:
                await self._render(user_id, action)
                return
        elif action.startswith("ads_objective_edit:"):
            _, object_type, object_id, campaign_scope = action.split(":", 3)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                if object_type == "CHANNEL" and not await self.repo.channel_owned(user_id, object_id):
                    return
                self._ads_objective_pending[str(user_id)] = (object_type, object_id, None if campaign_scope == "all" else campaign_scope)
                await self._render(user_id, f"ads_objective_input:{object_type}:{object_id}")
                return
        elif action.startswith("ads_objective_disable:"):
            _, object_type, object_id = action.split(":", 2)
            if can_access_ads(str(user_id)) and object_type in {"CHANNEL", "BOT"}:
                if object_type == "CHANNEL" and not await self.repo.channel_owned(user_id, object_id):
                    return
                async with self.session_factory() as db:
                    await disable_objective(db, object_type=object_type, object_id=object_id)
                    await db.commit()
                await self._render(user_id, f"ads_objective:{object_type}:{object_id}")
                return
        elif action.startswith(("ads_bot_campaigns:", "ads_bot_creatives:", "ads_bot_recommendations:", "ads_bot_context:", "ads_bot_learning:", "ads_bot_history:")):
            if can_access_ads(str(user_id)) and action.split(":", 1)[1]:
                await self._render(user_id, action)
                return
        elif action.startswith("ads_status:"):
            selected_id = action.split(":", 1)[1]
            if can_access_ads(str(user_id)) and await self.repo.channel_owned(user_id, selected_id):
                await self._render(user_id, f"ads:{selected_id}", selected_id)
        elif action == "ads_campaigns":
            # MAX may deliver a stale callback with the action prefix only
            # after the valid paginated screen has already been rendered.
            # Do not overwrite the working campaign list with a dead-end
            # "function unavailable" screen.
            logger.info("MAX stale campaign callback ignored")
            return
        elif action.startswith("ads_campaigns:") or action.startswith(("ads_creatives:", "ads_recommendations:", "ads_memory:", "ads_context:", "ads_learning:", "ads_history:", "ads_health:")):
            selected_id = action.split(":", 1)[1].split(":", 1)[0]
            ads_allowed = can_access_ads(str(user_id)) and await self.repo.channel_owned(user_id, selected_id)
            logger.warning(
                "MAX_CAMPAIGN_ROUTE allowed=%s selected_id_present=%s",
                ads_allowed,
                bool(selected_id),
            )
            if ads_allowed:
                await self._render(user_id, action, selected_id)
                return
            elif action.split(":", 1)[0] in {"ads_creatives", "ads_context", "ads_learning", "ads_history"}:
                # A visible dashboard can outlive a channel record/id refresh.
                # Resolve the current owned channel before falling through to
                # the generic unavailable screen.
                current_text = update.message.text if update.message else ""
                match = re.search(r"(?:Канал:|📢)\s*(.+?)(?:\n|$)", current_text)
                title = match.group(1).strip() if match else ""
                async with self.session_factory() as db:
                    recovered = None
                    if title:
                        recovered = await db.scalar(select(Channel).join(Admin).where(
                            Channel.title == title, Admin.external_id == str(user_id)
                        ))
                    if not recovered:
                        tracked_id = self._user_channels.get(str(user_id))
                        if tracked_id:
                            recovered = await db.scalar(select(Channel).join(Admin).where(
                                Channel.id == tracked_id, Admin.external_id == str(user_id)
                            ))
                if recovered and can_access_ads(str(user_id)):
                    await self._render(user_id, f"{action.split(':', 1)[0]}:{recovered.id}", str(recovered.id))
                    return
        elif action.startswith("ads_memory_detail:"):
            _, _, channel_id, _ = action.split(":", 3)
            if can_access_ads(str(user_id)) and await self.repo.channel_owned(user_id, channel_id):
                await self._render(user_id, action, channel_id)
        elif action.startswith("ads_memory_status:"):
            _, _, channel_id, memory_id, status = action.split(":", 4)
            if can_access_ads(str(user_id)) and await self.repo.channel_owned(user_id, channel_id):
                async with self.session_factory() as db:
                    await set_status(db, memory_id=memory_id, channel_id=channel_id, status=status)
                await self._render(user_id, f"ads_memory_detail:{channel_id}:{memory_id}", channel_id)
        elif action.startswith("ads_campaign_select:"):
            _, channel_id, campaign_id = action.split(":", 2)
            if can_access_ads(str(user_id)) and await self.repo.channel_owned(user_id, channel_id):
                async with self.session_factory() as db:
                    await toggle_campaign_link(db, channel_id=channel_id, campaign_id=campaign_id)
                await self._render(user_id, f"ads_campaigns:{channel_id}", channel_id)
                return
        elif action.startswith("promotion_bot"):
            bot_id = action.split(":", 1)[1] if ":" in action else None
            if bot_id and await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, action)
        elif action.startswith("promotion_landing:"):
            selected_id = action.split(":", 1)[1]
            if await self.repo.channel_owned(user_id, selected_id):
                await self._render(user_id, action, selected_id)
        elif action.startswith("promotion_create_landing:"):
            selected_id = action.split(":", 1)[1]
            if await self.repo.channel_owned(user_id, selected_id):
                await self.repo.create_landing(user_id, selected_id)
                await self._render(user_id, f"promotion_landing:{selected_id}", selected_id)
        elif action.startswith("promotion_yandex_check:"):
            selected_id = action.split(":", 1)[1]
            if await self.repo.channel_owned(user_id, selected_id):
                await self._render(user_id, f"promotion_yandex:{selected_id}", selected_id)
        elif action.startswith("promotion_analytics:") or action.startswith("promotion_yandex:") or action.startswith("promotion_settings:"):
            selected_id = action.split(":", 1)[1]
            if await self.repo.channel_owned(user_id, selected_id):
                await self._render(user_id, action, selected_id)
        elif action == "connect_bot":
            # Show the explanatory bot screen before entering token setup.
            # The token-waiting state is set only after the owner continues.
            await self._render(user_id, "connect_bot_intro")
        elif action == "custom_bot_token":
            if await self.repo.set_custom_bot_state(user_id, "waiting_custom_bot_token"):
                await self._render(user_id, "custom_bot_token")
        elif action == "custom_bot_help":
            # Keep token input state active while the owner reads the short help.
            await self.repo.set_custom_bot_state(user_id, "waiting_custom_bot_token")
            await self._render(user_id, "custom_bot_help")
        elif action == "bots":
            await self.repo.set_custom_bot_state(user_id, None)
            # Bots are listed only in the unified promotion screen.
            await self._render(user_id, "promotion")
        elif action == "my_bots":
            await self.repo.set_custom_bot_state(user_id, None)
            await self._render(user_id, "promotion")
        elif action.startswith("custom_bot_texts:") or action.startswith("custom_bot_dialogs:") or action.startswith("custom_bot_stats:"):
            bot_id = action.split(":", 1)[1]
            if await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, action)
        elif action.startswith("custom_bot_broadcast:"):
            bot_id = action.split(":", 1)[1]
            if await self.repo.bot_owned(user_id, bot_id):
                await self.repo.set_custom_bot_state(user_id, f"broadcast:{bot_id}", bot_id)
                await self._render(user_id, action)
        elif action.startswith("custom_bot_broadcast_cancel:"):
            bot_id = action.split(":", 1)[1]
            await self.repo.set_custom_bot_state(user_id, None)
            if await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, f"custom_bot:{bot_id}")
        elif action.startswith("custom_bot_broadcast_send:"):
            bot_id = action.split(":", 1)[1]
            bot = await self.repo.bot_owned(user_id, bot_id)
            draft = self._broadcast_drafts.pop(str(user_id), "")
            if bot and draft and isinstance(self.client, RealMaxClient):
                custom = self._custom_clients.get(bot.id)
                if not custom:
                    custom = RealMaxClient(decrypt_token(bot.encrypted_token), self.client.base_url)
                    self._custom_clients[bot.id] = custom
                for recipient in await self.repo.custom_bot_recipients(user_id, bot.id):
                    await custom.send_message(recipient, draft)
                await self.repo.set_custom_bot_state(user_id, None)
                await self._render(user_id, f"custom_bot:{bot.id}")
        elif action.startswith("custom_bot_disable:"):
            bot_id = action.split(":", 1)[1]
            if await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, f"custom_bot_disable_confirm:{bot_id}")
        elif action.startswith("custom_bot_disable_confirmed:"):
            bot_id = action.split(":", 1)[1]
            if await self.repo.bot_owned(user_id, bot_id):
                await self.repo.set_custom_bot_status(user_id, bot_id, "DISABLED")
                await self._render(user_id, "my_bots")
        elif action.startswith("custom_bot_enable:"):
            bot_id = action.split(":", 1)[1]
            if await self.repo.bot_owned(user_id, bot_id):
                await self.repo.set_custom_bot_status(user_id, bot_id, "ACTIVE")
                await self._render(user_id, f"custom_bot:{bot_id}")
        elif action.startswith("custom_bot_settings:"):
            bot_id = action.split(":", 1)[1]
            if await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, action)
        elif action.startswith("custom_bot_template:"):
            _, bot_id, kind = action.split(":", 2)
            if await self.repo.bot_owned(user_id, bot_id):
                await self.repo.set_custom_bot_state(user_id, f"custom_template:{bot_id}:{kind}", bot_id)
                await self._render(user_id, action)
        elif action.startswith("custom_bot_template_view:"):
            _, bot_id, kind = action.split(":", 2)
            if await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, action)
        elif action.startswith("custom_bot_template_reset:"):
            _, bot_id, kind = action.split(":", 2)
            if await self.repo.bot_owned(user_id, bot_id):
                if await self.repo.reset_custom_bot_template(user_id, bot_id, kind):
                    await self._render(user_id, f"custom_bot_template_view:{bot_id}:{kind}")
        elif action.startswith("custom_bot_template_cancel:"):
            _, bot_id, kind = action.split(":", 2)
            await self.repo.set_custom_bot_state(user_id, None)
            if await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, f"custom_bot_template_view:{bot_id}:{kind}")
        elif action == "custom_bot_cancel":
            await self.repo.set_custom_bot_state(user_id, None)
            await self._render(user_id, "my_bots")
        elif action.startswith("custom_bot_open:"):
            bot_id = action.split(":", 1)[1]
            bot = await self.repo.bot_owned(user_id, bot_id)
            if bot:
                await self._render(user_id, f"custom_bot_opening:{bot.id}")
        elif action.startswith("custom_bot:"):
            bot_id = action.split(":", 1)[1]
            if await self.repo.bot_owned(user_id, bot_id):
                await self._render(user_id, action)
        elif action.startswith("custom_bot_delete:"):
            bot_id = action.split(":", 1)[1]
            if await self.repo.delete_custom_bot(user_id, bot_id):
                await self._render(user_id, "my_bots")
        elif action.startswith("custom_bot_refresh:"):
            bot_id = action.split(":", 1)[1]
            # bot_id is persisted separately in pending_custom_bot_id;
            # custom_bot_state is VARCHAR(32), so keep the state compact.
            if await self.repo.set_custom_bot_state(user_id, "refresh", bot_id):
                await self._render(user_id, "custom_bot_refresh_token")
        elif action == "custom_bot_refresh_cancel":
            await self.repo.set_custom_bot_state(user_id, None)
            await self._render(user_id, "my_bots")
        elif action.startswith("custom_bot_found:"):
            await self._render(user_id, action)
        elif action.startswith("custom_bot_error"):
            await self._render(user_id, action)
        # Custom-bot callbacks are fully handled above.  Do not let them fall
        # through to the generic channel/navigation renderer, which strips
        # the bot id from custom_bot:<id> and renders the fallback screen.
        if action.startswith("custom_bot") or action.startswith("promotion") or action in {"bots", "my_bots", "connect_bot", "custom_bot_cancel"}:
            return
        if action.startswith("receiving:") and channel_id and len(action.split(":")) > 2:
            accepting = action.split(":", 2)[2] == "on"
            await self.repo.set_receiving(channel_id, accepting)
            await self._render(user_id, f"channel:{channel_id}", channel_id)
            screen = f"channel:{channel_id}"
        elif action.startswith("daily_reports:"):
            bits = action.split(":", 2)
            channel_id = bits[1] if len(bits) > 1 else None
            enabled = len(bits) > 2 and bits[2] == "on"
            if channel_id and await self.repo.channel_owned(user_id, channel_id):
                await self.repo.set_daily_reports(channel_id, enabled)
                await self._render(user_id, f"promotion_channel:{channel_id}", channel_id)
        elif action == "messages:global":
            logger.warning("MESSAGES_SCREEN_OPEN callback_id=%s admin_id=%s", update.callback_id, user_id)
            await self._open_global_feed(user_id)
        elif action.startswith("messages:"):
            bits = action.split(":")
            channel_id = bits[1] if len(bits) > 1 else None
            await self._open_native_feed(user_id, channel_id)
        elif action.startswith("inbox_connect:") or action.startswith("inbox_change:"):
            channel_id = action.split(":", 1)[1] if ":" in action else None
            if channel_id and await self.repo.begin_inbox_connect(user_id, channel_id):
                await self._render(user_id, action, channel_id)
        elif action.startswith("inbox_cancel:"):
            channel_id = await self.repo.cancel_inbox_connect(user_id)
            await self._render(user_id, f"channel:{channel_id}" if channel_id else "channels", channel_id)
        elif action.startswith("delete:"):
            channel_id = action.split(":", 1)[1] if ":" in action else None
            if channel_id:
                await self._render(user_id, f"delete_confirm:{channel_id}", channel_id)
        elif action.startswith("delete_cancel:"):
            channel_id = action.split(":", 1)[1] if ":" in action else None
            if channel_id:
                await self._render(user_id, f"promotion_channel:{channel_id}", channel_id)
        elif action.startswith("delete_channel_confirm:"):
            channel_id = action.split(":", 1)[1] if ":" in action else None
            if channel_id:
                await self._delete_channel(user_id, channel_id)
        elif action == "read_all":
            await self.repo.mark_all_read(user_id)
            await self._render(user_id, "messages:global")
        elif action.startswith("feed_older:"):
            bits = action.split(":")
            channel_id = bits[1] if len(bits) > 1 else None
            await self._open_native_feed(user_id, channel_id, limit=40, refresh=True)
        elif action.startswith("conversation:"):
            await self._render(user_id, action)
        elif action.startswith("reply:"):
            conversation_id = action.split(":", 1)[1]
            if await self.repo.begin_reply(user_id, conversation_id):
                await self._render(user_id, action)
        elif action.startswith("cancel_reply:"):
            conversation_id = action.split(":", 1)[1]
            await self.repo.clear_reply(user_id)
            await self._render(user_id, f"conversation:{conversation_id}:0")
        elif action.startswith("block:") or action.startswith("unblock:"):
            conversation_id = action.split(":", 1)[1]
            blocked = action.startswith("block:")
            if await self.repo.set_blocked(user_id, conversation_id, blocked):
                await self._render(user_id, f"conversation:{conversation_id}:0")
        elif action == "about":
            await self._render(user_id, "about")
        elif action == "owner_admin":
            await self._render(user_id, "owner_admin")
        elif action == "owner_clients":
            await self._render(user_id, "owner_clients")
        elif action == "support_start":
            # Keep old callback messages safe: route them to the same direct
            # personal-support screen instead of reopening the legacy ticket.
            await self._render(user_id, "support_direct")
        elif action.startswith("support_continue:"):
            # Old ticket cards may still be visible in users' histories.
            # They must not reopen the retired ticket workflow.
            await self._render(user_id, "support_direct")
        elif action == "support_cancel":
            await self.repo.clear_support_input(user_id)
            await self._render(user_id, "about")
        elif screen == "template_view" and channel_id and len(action.split(":")) > 2:
            template_type = action.split(":", 2)[2]
            await self._render(user_id, f"template_view:{channel_id}:{template_type}", channel_id)
        elif screen == "template" and channel_id and len(action.split(":")) > 2:
            template_type = action.split(":", 2)[2]
            current = await self.repo.begin_template_edit(user_id, channel_id, template_type)
            if current is not None:
                await self._render(user_id, f"template_prompt:{channel_id}:{template_type}", channel_id)
        elif screen == "cancel_template":
            channel_id = await self.repo.cancel_template(user_id)
            await self._render(user_id, f"settings:{channel_id}", channel_id)
        elif screen == "reset_template" and channel_id:
            template_type = action.split(":", 2)[2] if len(action.split(":")) > 2 else "welcome"
            await self.repo.reset_template(user_id, channel_id, template_type)
            await self._render(user_id, f"settings:{channel_id}", channel_id)
        else:
            if action.startswith("channel:") or action in {"home", "channels", "bots"}:
                # The regular admin screens share one service message.  Only
                # the temporary messages-feed controls need to be closed
                # before navigating home; deleting the current service panel
                # here would force _render() to create a new message and
                # change its native MAX timestamp.
                _, messages_controls_id = await self.repo.messages_ui_ids(user_id)
                if action == "home" and callback_message_id == messages_controls_id:
                    await self._close_messages_ui(user_id)
                await self.repo.end_feed_session(user_id)
            await self._render(user_id, screen, channel_id)
    async def _render(self, admin_id: str, screen: str, channel_id: str | None = None, force_new: bool = False):
        text, keyboard, stored_message_id, media = await self.repo.render(admin_id, screen, channel_id)
        # AI promotion results are the only dashboard messages whose text is
        # explicitly treated as untrusted model output.  Keep preview
        # suppression scoped to this screen; regular MAXIBOT screens may
        # retain their existing preview behavior.
        ai_result_screen = screen.startswith("ads:")
        if stored_message_id and not force_new:
            try:
                edit_kwargs = {"disable_link_preview": True} if ai_result_screen else {}
                await self.client.edit_message(
                    admin_id, stored_message_id, text, keyboard, media, **edit_kwargs
                )
                logger.warning("ADMIN_UI_EDIT_OK screen=%s message_id=%s", screen, stored_message_id)
                return stored_message_id
            except Exception as exc:
                logger.warning(
                    "ADMIN_UI_EDIT_FAILED screen=%s message_id=%s error=%s",
                    screen, stored_message_id, exc,
                )
                if media:
                    try:
                        await self.client.delete_message(stored_message_id)
                    except Exception:
                        logger.info("MAX old service UI delete failed after media edit failure")
        if stored_message_id and force_new:
            try:
                await self.client.delete_message(stored_message_id)
                logger.warning("MAX service UI replaced old_message_id=%s", stored_message_id)
            except Exception:
                logger.info("MAX old service UI delete failed; continuing with replacement")
        await_kwargs = {"disable_link_preview": True} if ai_result_screen else {}
        new_id = await self.client.send_message(admin_id, text, keyboard, media, **await_kwargs)
        await self.repo.save_service_message(admin_id, new_id)
        logger.warning("ADMIN_UI_FALLBACK_CREATE screen=%s new_message_id=%s", screen, new_id)
        return new_id

    @staticmethod
    def _support_status_label(status: str) -> str:
        return {"NEW": "🔵 Новое", "IN_PROGRESS": "🟡 В работе", "CLOSED": "✅ Закрыто"}.get(status, status)

    @classmethod
    def _support_card_text(cls, ticket, text: str | None = None) -> str:
        body = ticket.source_text if text is None else text
        return (
            f"🎫 #{ticket.display_number} · {cls._support_status_label(ticket.status)}\n\n"
            f"👤 {ticket.source_user_name}\n\n{body or 'Вложение'}"
        )

    @staticmethod
    def _support_card_buttons(status: str, ticket_id: str) -> list[list[dict]]:
        if status == "CLOSED":
            return []
        if status == "NEW":
            return [[{"text": "🟡 В работу", "callback_data": f"support_take:{ticket_id}"}],
                    [{"text": "✅ Закрыть", "callback_data": f"support_close:{ticket_id}"}]]
        return [[{"text": "✅ Закрыть", "callback_data": f"support_close:{ticket_id}"}]]

    async def _update_support_card(self, ticket) -> None:
        if not ticket.support_chat_message_id:
            return
        try:
            attachments = json.loads(ticket.attachments_json or "[]")
        except (TypeError, ValueError):
            attachments = []
        await self.client.edit_message(
            ticket.support_chat_id,
            ticket.support_chat_message_id,
            self._support_card_text(ticket),
            self._support_card_buttons(ticket.status, ticket.id),
            attachments,
        )

    async def _support_status_callback(self, update: MaxUpdate, action: str) -> None:
        staff_id = self._event_user_id(update)
        if not staff_id or not await self.repo.is_admin(staff_id):
            return
        ticket_id = action.split(":", 1)[1] if ":" in action else ""
        ticket = await self.repo.support_ticket_by_message(update.message.external_message_id) if update.message else None
        if not ticket or ticket.id != ticket_id:
            logger.warning("SUPPORT_TICKET_MAPPING_NOT_FOUND ticket_id=%s", ticket_id)
            return
        if action.startswith("support_take:"):
            if ticket.status != "NEW":
                return
            ticket = await self.repo.support_ticket_status(ticket.id, "IN_PROGRESS")
            logger.warning("SUPPORT_TICKET_TAKEN ticket_id=%s", ticket.id)
        elif action.startswith("support_close:"):
            if ticket.status == "CLOSED":
                return
            ticket = await self.repo.support_ticket_status(ticket.id, "CLOSED")
            try:
                await self.client.send_message(ticket.max_user_id, f"✅ Обращение #{ticket.display_number} закрыто.\n\nЕсли появится новый вопрос, вы можете создать новое обращение через:\nО сервисе → Написать в поддержку.")
                logger.warning("SUPPORT_TICKET_CLOSED ticket_id=%s", ticket.id)
            except Exception:
                logger.exception("SUPPORT_TICKET_CLOSE_NOTICE_FAILED ticket_id=%s", ticket.id)
        if ticket:
            try:
                await self._update_support_card(ticket)
            except Exception:
                logger.exception("SUPPORT_TICKET_CARD_UPDATE_FAILED ticket_id=%s", ticket.id)

    async def _handle_support_input(self, admin_id: str, message: MaxMessage, user_name: str) -> bool:
        mode, existing_ticket_id = await self.repo.support_input(admin_id)
        if not mode:
            return False
        support_chat_id = (settings.max_support_chat_id or "").strip()
        if not support_chat_id:
            await self.repo.clear_support_input(admin_id)
            await self.client.send_message(admin_id, "⚠️ Поддержка временно недоступна.\nПопробуйте немного позже.")
            logger.warning("SUPPORT_CHAT_NOT_CONFIGURED")
            return True
        text = (message.text or "").strip()
        attachments = message.attachments or []
        if not text and not attachments:
            return True
        ticket = None
        if existing_ticket_id:
            ticket = await self.repo.support_ticket_by_id(existing_ticket_id)
        if ticket is None:
            ticket = await self.repo.create_support_ticket(admin_id, user_name, text, attachments, support_chat_id)
        if not ticket:
            return True
        if existing_ticket_id and ticket:
            card_text = f"↪️ Дополнение к #{ticket.display_number}\n👤 {user_name}\n\n{text or 'Вложение'}"
        else:
            card_text = self._support_card_text(ticket)
        try:
            support_message_id = await self.client.send_chat_message(
                support_chat_id,
                card_text,
                attachments,
                self._support_card_buttons(ticket.status, ticket.id) if not existing_ticket_id else [],
            )
            if ticket.support_chat_message_id is None:
                await self.repo.set_support_ticket_message(ticket.id, support_message_id, message.external_message_id)
                logger.warning("SUPPORT_TICKET_CREATED ticket_id=%s number=%s", ticket.id, ticket.display_number)
                logger.warning("SUPPORT_TICKET_FORWARDED ticket_id=%s", ticket.id)
                notice = f"✅ Обращение #{ticket.display_number} создано.\n\nОтвет поддержки придёт сюда же."
            else:
                await self.repo.add_support_message_mapping(ticket.id, support_message_id, admin_id, "in", message.external_message_id)
                logger.warning("SUPPORT_TICKET_MESSAGE_ADDED ticket_id=%s", ticket.id)
                notice = f"✅ Сообщение добавлено в обращение #{ticket.display_number}."
            await self.repo.clear_support_input(admin_id)
            await self.client.send_message(admin_id, notice)
        except Exception:
            await self.repo.mark_support_delivery_failed(ticket.id)
            logger.exception("SUPPORT_TICKET_DELIVERY_FAILED ticket_id=%s", ticket.id)
            await self.client.send_message(admin_id, "⚠️ Не удалось отправить обращение в поддержку.\nПопробуйте ещё раз позже.")
        return True

    async def _handle_support_reply(self, update: MaxUpdate, message: MaxMessage, reply_id: str) -> bool:
        ticket = await self.repo.support_ticket_by_message(reply_id)
        if not ticket:
            logger.warning("SUPPORT_REPLY_MAPPING_NOT_FOUND linked_message_id=%s", reply_id)
            return True
        if ticket.status == "NEW":
            ticket = await self.repo.support_ticket_status(ticket.id, "IN_PROGRESS")
            try:
                await self._update_support_card(ticket)
            except Exception:
                logger.exception("SUPPORT_TICKET_CARD_UPDATE_FAILED ticket_id=%s", ticket.id)
        text = (message.text or "").strip()
        try:
            await self.client.send_feed_message(ticket.max_user_id, f"💬 Поддержка MAXIBOT · #{ticket.display_number}\n\n{text or 'Вложение'}", message.attachments or [])
            await self.repo.add_support_message_mapping(ticket.id, message.external_message_id, ticket.max_user_id, "out", reply_id)
            logger.warning("SUPPORT_REPLY_RECEIVED ticket_id=%s", ticket.id)
            logger.warning("SUPPORT_REPLY_SENT ticket_id=%s", ticket.id)
        except Exception:
            logger.exception("SUPPORT_REPLY_DELIVERY_FAILED ticket_id=%s", ticket.id)
        return True

    async def _handle_inbox_reply(self, update: MaxUpdate, message: MaxMessage, reply_id: str) -> bool:
        """Route a native Reply made in a channel's physical inbox chat."""
        inbox_chat_id = str(update.chat_id or "")
        logger.warning(
            "INBOX_REPLY_RECEIVED inbox_chat_id=%s reply_message_id=%s linked_source_message_id=%s",
            inbox_chat_id, message.external_message_id, reply_id,
        )
        mapping = await self.repo.inbox_mapping_for_reply(inbox_chat_id, reply_id)
        if not mapping:
            logger.warning("INBOX_REPLY_MAPPING_NOT_FOUND linked_source_message_id=%s", reply_id)
            return True
        channel = await self.repo.channel_by_inbox_chat(inbox_chat_id)
        if not channel or channel.id != mapping.channel_id:
            logger.warning("INBOX_REPLY_MAPPING_NOT_FOUND linked_source_message_id=%s", reply_id)
            return True
        source_private_chat_id = str(mapping.source_user_id)
        logger.warning(
            "INBOX_REPLY_MAPPING_FOUND channel_id=%s source_private_chat_id=%s",
            channel.id, source_private_chat_id,
        )
        text = (message.text or "").strip()
        attachments = message.attachments or []
        if not text and not attachments:
            return True
        outbound_text = f"📢 {channel.title}\nОтвет администратора:\n\n{text}" if text else f"📢 {channel.title}\nОтвет администратора:\n\nВложение"
        try:
            await self.client.send_feed_message(source_private_chat_id, outbound_text, attachments)
            await self.repo.save_inbox_reply(mapping, outbound_text, attachments)
            logger.warning(
                "INBOX_REPLY_SENT channel_id=%s source_private_chat_id=%s",
                channel.id, source_private_chat_id,
            )
        except Exception:
            logger.exception(
                "INBOX_REPLY_SEND_FAILED channel_id=%s source_private_chat_id=%s",
                channel.id, source_private_chat_id,
            )
        return True

    async def _leave_chat_with_retry(self, chat_id: str, kind: str) -> bool:
        for attempt in range(1, 4):
            try:
                await self.client.leave_chat(chat_id)
                logger.warning("CHANNEL_%s_LEFT chat_id=%s", kind, chat_id)
                return True
            except Exception as exc:
                if attempt < 3:
                    await asyncio.sleep(attempt)
                    continue
                logger.exception(
                    "CHANNEL_DELETE_EXTERNAL_LEAVE_FAILED kind=%s chat_id=%s attempts=%s error=%s",
                    kind, chat_id, attempt, type(exc).__name__,
                )
        return False

    async def _delete_channel(self, admin_id: str, channel_id: str) -> None:
        snapshot = await self.repo.channel_delete_snapshot(admin_id, channel_id)
        if not snapshot:
            logger.info("CHANNEL_DELETE_ALREADY_REMOVED channel_id=%s", channel_id)
            await self._render(admin_id, "channels")
            return
        logger.warning("CHANNEL_DELETE_STARTED channel_id=%s", channel_id)
        leave_ids: set[str] = set()
        inbox_chat_id = snapshot.get("inbox_chat_id")
        channel_chat_id = snapshot.get("external_id")
        if inbox_chat_id:
            leave_ids.add(str(inbox_chat_id))
        if channel_chat_id:
            leave_ids.add(str(channel_chat_id))
        for chat_id in leave_ids:
            kind = "INBOX_BOT" if str(chat_id) == str(inbox_chat_id) else "CHANNEL_BOT"
            if not await self._leave_chat_with_retry(str(chat_id), kind):
                from urllib.parse import quote as _quote
                await self._render(admin_id, f"channel_delete_failed:{_quote(snapshot['title'], safe='')}")
                return
        deleted = await self.repo.delete_channel_owned(admin_id, channel_id)
        if not deleted:
            logger.info("CHANNEL_DELETE_ALREADY_REMOVED channel_id=%s", channel_id)
            await self._render(admin_id, "channels")
            return
        logger.warning("CHANNEL_DATA_DELETED channel_id=%s", channel_id)
        logger.warning("CHANNEL_DELETE_COMPLETED channel_id=%s", channel_id)
        await self._render(admin_id, f"channel_deleted:{quote(deleted['title'], safe='')}")

    async def _delete_feed_messages(self, session_id: str | None) -> None:
        # Feed messages are permanent: their MAX server timestamp and native
        # Reply mapping must survive navigation and future feed sessions.
        return

    async def _open_global_inbox(self, admin_id: str) -> None:
        await self.repo.mark_all_read(admin_id)
        await self._render(admin_id, "messages:global")

    async def _open_global_feed(self, admin_id: str, limit: int = 20, refresh: bool = False) -> None:
        active_channel, _ = await self.repo.active_feed(admin_id)
        if active_channel == "__global__" and not refresh:
            return
        await self._close_messages_ui(admin_id)
        rows = await self.repo.global_feed_rows(admin_id, None)
        await self.repo.set_global_feed_open(admin_id)
        logger.warning("MAX global native feed pending_rows=%s", len(rows))
        native_sent = 0
        mapped_skipped = 0
        delivery_failed = False
        for row in rows:
            if await self.repo.feed_mapping_id(row.id):
                mapped_skipped += 1
                logger.info("MAX global native feed mapped source skipped")
                continue
            channel = row.conversation.channel
            try:
                attachments = json.loads(row.attachments_json or "[]")
            except (TypeError, ValueError):
                attachments = []
            try:
                if native_sent:
                    await asyncio.sleep(0.55)
                feed_message_id = await self.client.send_feed_message(
                    admin_id,
                    _feed_caption(row, channel.title),
                    attachments,
                )
                await self.repo.save_feed_mapping(
                    feed_message_id,
                    row.id,
                    row.external_message_id,
                    row.conversation.user_external_id,
                    channel.id,
                    "global",
                )
                native_sent += 1
            except Exception:
                delivery_failed = True
                logger.exception("MAX global native feed delivery failed")
        if not delivery_failed:
            await self.repo.mark_all_read(admin_id)
        logger.warning(
            "MAX global native feed result rows=%s native_sent=%s mapped_skipped=%s",
            len(rows), native_sent, mapped_skipped,
        )
        controls = [[{"text": "⬅️ Назад", "callback_data": "home"}]]
        # MAX requires a host message for an inline keyboard. Keep it
        # deliberately compact and temporary; it is not part of the feed.
        control_id = await self.client.send_message(admin_id, "·", controls)
        await self.repo.save_messages_controls(admin_id, control_id)
        await self.repo.save_service_message(admin_id, control_id)

    async def _close_messages_ui(self, admin_id: str) -> None:
        header_id, controls_id = await self.repo.messages_ui_ids(admin_id)
        current_service_id = await self.repo.service_message_id(admin_id)
        ui_ids = {value for value in (header_id, controls_id) if value}
        # The former HOME panel is also temporary UI. Remove it when entering
        # the messages screen, but never infer deletability from message text.
        if current_service_id and current_service_id not in ui_ids:
            ui_ids.add(current_service_id)
        for message_id in dict.fromkeys(ui_ids):
            try:
                await self.client.delete_message(message_id)
                logger.warning("MESSAGES_UI_DELETE message_id=%s", message_id)
            except Exception:
                logger.exception("MESSAGES_UI_DELETE_FAILED message_id=%s", message_id)
        if header_id or controls_id:
            await self.repo.clear_messages_ui(admin_id)

    async def _open_native_feed(self, admin_id: str, channel_id: str | None, limit: int = 20, refresh: bool = False) -> None:
        if not channel_id:
            await self._render(admin_id, "channels")
            return
        active_channel, active_session = await self.repo.active_feed(admin_id)
        if active_channel == channel_id and active_session and not refresh:
            return
        old_session, session_id = await self.repo.begin_feed_session(admin_id, channel_id)
        await self.repo.mark_channel_read(admin_id, channel_id)
        await self._render(admin_id, f"feed_header:{channel_id}", channel_id)
        rows = await self.repo.feed_rows(admin_id, channel_id, limit + 1)
        has_older = len(rows) > limit
        rows = rows[-limit:] if has_older else rows
        for row in rows:
            if row.direction == "in" and await self.repo.feed_mapping_id(row.id):
                continue
            conversation = row.conversation
            caption = _feed_caption(row, channel.title)
            try:
                attachments = json.loads(row.attachments_json or "[]")
            except (TypeError, ValueError):
                attachments = []
            try:
                feed_message_id = await self.client.send_feed_message(admin_id, caption, attachments)
                if row.direction == "in":
                    await self.repo.save_feed_mapping(
                        feed_message_id,
                        row.id,
                        row.external_message_id,
                        conversation.user_external_id,
                        channel_id,
                        session_id,
                    )
            except Exception:
                logger.exception("MAX native feed delivery failed")
        controls = []
        if has_older:
            controls.append([{"text": "⬆️ Ранее", "callback_data": f"feed_older:{channel_id}:{limit}"}])
        controls.append([{"text": "⬅️ Назад", "callback_data": f"channel:{channel_id}"}])
        try:
            await self._close_messages_ui(admin_id)
            control_id = await self.client.send_message(admin_id, "·", controls)
            await self.repo.save_messages_controls(admin_id, control_id)
            await self.repo.save_service_message(admin_id, control_id)
        except Exception:
            logger.exception("MAX feed controls delivery failed")

    async def _deliver_native_feed_message(self, admin_id: str, channel_id: str, message_id: str) -> None:
        active_channel, _ = await self.repo.active_feed(admin_id)
        if active_channel != "__global__":
            return
        if await self.repo.feed_mapping_id(message_id):
            return
        async with self.session_factory() as db:
            row = await db.scalar(select(Message).options(selectinload(Message.conversation).selectinload(Conversation.channel)).where(Message.id == message_id))
        if not row:
            return
        conversation = row.conversation
        caption = _feed_caption(row, row.conversation.channel.title)
        try:
            attachments = json.loads(row.attachments_json or "[]")
        except (TypeError, ValueError):
            attachments = []
        try:
            feed_message_id = await self.client.send_feed_message(admin_id, caption, attachments)
            await self.repo.save_feed_mapping(feed_message_id, row.id, row.external_message_id, conversation.user_external_id, channel_id, "global")
            await self.repo.mark_all_read(admin_id)
        except Exception:
            logger.exception("MAX live native feed delivery failed")

    async def _channel_admin_done(self, owner_id: str, channel_external_id: str) -> None:
        """Finish channel onboarding after the owner grants admin rights."""
        try:
            chat = await self.client.get_chat(channel_external_id)
            admins = await self.client.get_admins(channel_external_id)
            bot_profile = await self.client.get_me()
        except Exception:
            await self.client.send_message(owner_id, "⚠️ Не удалось проверить права бота. Попробуйте нажать «Готово» ещё раз.")
            return
        bot_id = bot_profile.get("user_id") or bot_profile.get("id") if isinstance(bot_profile, dict) else None
        members = admins.get("members", []) if isinstance(admins, dict) else []
        is_admin = bool(bot_id) and any(
            isinstance(member, dict) and str(member.get("user_id") or member.get("id")) == str(bot_id)
            for member in members
        ) if isinstance(members, list) else False
        if not is_admin:
            await self.client.send_message(
                owner_id,
                "⚠️ Бот всё ещё не является администратором.\n\n"
                "Назначьте MAXIBOT администратором канала и нажмите «Готово» ещё раз.",
                [[
                    {"text": "✅ Готово", "callback_data": f"channel_admin_done:{channel_external_id}"},
                    {"text": "❌ Отмена", "callback_data": f"channel_admin_cancel:{channel_external_id}"},
                ]],
            )
            return
        result = await self.repo.connect_channel_from_bot_added(channel_external_id, owner_id, chat)
        if result.get("status") in {"created", "updated", "duplicate"}:
            await self.repo.activate_pending_free_trial(str(owner_id), str(result["channel_id"]))
            await self.client.send_message(owner_id, f"✅ Канал «{result.get('title') or 'MAX'}» подключён к MAXIBOT.")
            await self._render(owner_id, "channels")
        else:
            await self.client.send_message(owner_id, "⚠️ Не удалось завершить подключение канала. Попробуйте ещё раз.")

    async def _bot_added(self, update: MaxUpdate):
        if not update.chat_id or update.bot_connection_id:
            return
        logger.warning(
            "BOT_ADDED_CHAT_DISCOVERY chat_id=%s initiator_present=%s pending_inbox=%s",
            update.chat_id,
            bool(update.user_id),
            bool(await self.repo.pending_inbox_channel(update.user_id)) if update.user_id else False,
        )
        raw = update.raw or {}
        nested_chat = raw.get("chat") if isinstance(raw.get("chat"), dict) else {}
        is_channel_event = bool(
            raw.get("is_channel")
            or raw.get("chat_type") == "channel"
            or raw.get("type") == "channel"
            or nested_chat.get("type") == "channel"
            or nested_chat.get("chat_type") == "channel"
        )
        if is_channel_event:
            if update.update_type == "bot_removed":
                await self.repo.mark_max_channel_removed(update.chat_id, update.user_id)
                logger.info("MAX bot removed from channel chat_id=%s", update.chat_id)
                return
            try:
                chat = await self.client.get_chat(update.chat_id)
                admin_read_status = 200
                try:
                    admins = await self.client.get_admins(update.chat_id)
                except MaxApiError as exc:
                    # Admin listing is diagnostic only.  A 403 must not turn
                    # the fail-closed channel validation into a sync error.
                    admins = {}
                    admin_read_status = exc.status_code
                admin_members = admins.get("members", []) if isinstance(admins, dict) else []
                try:
                    bot_profile = await self.client.get_me()
                except Exception:
                    bot_profile = {}
                primary_bot_id = bot_profile.get("user_id") or bot_profile.get("id") if isinstance(bot_profile, dict) else None
                bot_is_admin = any(
                    isinstance(member, dict)
                    and str(member.get("user_id") or member.get("id")) == str(primary_bot_id)
                    for member in admin_members
                ) if isinstance(admin_members, list) and primary_bot_id else False
                initiator_in_admins = any(
                    isinstance(member, dict)
                    and str(member.get("user_id") or member.get("id")) == str(update.user_id)
                    for member in admin_members
                ) if isinstance(admin_members, list) else False
                initiator_is_owner = any(
                    isinstance(member, dict)
                    and str(member.get("user_id") or member.get("id")) == str(update.user_id)
                    and bool(member.get("is_owner"))
                    for member in admin_members
                ) if isinstance(admin_members, list) else False
                facts = official_bot_added_diagnostics(chat, update.user_id)
                facts.update({
                    "admins_read": isinstance(admin_members, list),
                    "admins_read_status": admin_read_status,
                    "admin_count": len(admin_members) if isinstance(admin_members, list) else None,
                    "initiator_in_admins": initiator_in_admins,
                    "initiator_is_owner": initiator_is_owner,
                })
                logger.warning(
                    "MAX_CHANNEL_AUTO_CONNECT_DIAGNOSTICS chat_id=%s facts=%s",
                    update.chat_id,
                    facts,
                )
                result = await self.repo.connect_channel_from_bot_added(update.chat_id, update.user_id, chat)
                logger.warning("MAX_CHANNEL_AUTO_CONNECT status=%s reason=%s",
                               result.get("status"), result.get("reason"))
                if result.get("status") in {"created", "updated", "duplicate"} and update.user_id:
                    system_conversion_status = await self.repo.queue_system_channel_connected(str(update.user_id))
                    logger.info("SYSTEM_CHANNEL_CONNECTED_CONVERSION status=%s", system_conversion_status)
                if result.get("status") in {"created", "updated", "duplicate"} and update.user_id:
                    if admin_read_status == 200 and not bot_is_admin:
                        await self.client.send_message(
                            str(update.user_id),
                            "⚠️ Бот добавлен, но не является администратором.\n\n"
                            "Пожалуйста, назначьте MAXIBOT администратором канала и нажмите «Готово».",
                            [[
                                {"text": "✅ Готово", "callback_data": f"channel_admin_done:{update.chat_id}"},
                                {"text": "❌ Отмена", "callback_data": f"channel_admin_cancel:{update.chat_id}"},
                            ]],
                        )
                        return
                    await self.repo.activate_pending_free_trial(str(update.user_id), str(result["channel_id"]))
                    await self.client.send_message(
                        str(update.user_id),
                        f"✅ Канал «{result.get('title') or 'MAX'}» подключён к MAXIBOT.",
                    )
                    await self._render(str(update.user_id), "channels")
            except Exception:
                logger.exception("MAX_CHANNEL_SYNC_FAILED")
            return
        initiator = update.user_id
        if update.update_type in {"bot_removed", "dialog_removed", "bot_stopped"}:
            # Removing MAXIBOT from the private bot dialog starts a new
            # onboarding cycle. Channel/group removal must never reset it.
            # MAX emits either dialog_removed or bot_stopped for the same
            # private-bot deletion depending on the client.  Channel events
            # have already returned through the is_channel_event branch, so
            # resetting here is limited to the private MAXIBOT dialog.
            private_dialog_event = update.update_type in {"dialog_removed", "bot_stopped"}
            private_bot_removed = (
                update.update_type == "bot_removed"
                and update.user_id and update.chat_id
                and str(update.user_id) == str(update.chat_id)
            )
            if private_dialog_event or private_bot_removed:
                await self.repo.mark_bot_recipient_blocked(str(update.user_id or update.chat_id), update.update_type)
                await self.repo.reset_onboarding(str(update.user_id))
                logger.info("MAX_PRIVATE_DIALOG_REMOVED_ONBOARDING_RESET event=%s", update.update_type)
            result = await self.repo.unbind_inbox_chat(update.chat_id)
            if result and result.get("admin_external_id"):
                await self.client.send_message(
                    result["admin_external_id"],
                    f"ℹ️ MAXIBOT удалён из чата «{result.get('title') or update.chat_id}».\n\n"
                    "Чат для обращений отключён. Его можно подключить заново в настройках канала.",
                )
            return
        if raw.get("is_channel") is not False:
            logger.info("MAX group bot_added rejected: is_channel flag is not false chat_id=%s", update.chat_id)
            if initiator:
                await self.client.send_message(initiator, "Не удалось определить групповой чат. Откройте «Подключить чат» и повторите добавление MAXIBOT.")
            return
        try:
            chat = await self.client.get_chat(update.chat_id)
        except Exception:
            logger.exception("MAX_GROUP_CHAT_METADATA_FAILED")
            if initiator:
                await self.client.send_message(initiator, "Не удалось получить данные чата MAX. Чат не подключён.")
            return
        chat_type = chat.get("chat_type") or chat.get("type") if isinstance(chat, dict) else None
        if str(chat_type or "").lower() not in {"chat", "group"}:
            logger.info("MAX group bot_added rejected: invalid chat type=%s chat_id=%s", chat_type, update.chat_id)
            if initiator:
                await self.client.send_message(initiator, "Этот объект MAX не является групповым чатом. Чат не подключён.")
            return
        pending_channel = await self.repo.pending_inbox_channel(initiator) if initiator else None
        if pending_channel:
            await self._bind_inbox_chat(initiator, pending_channel, update.chat_id)
            return
        candidates = await self.repo.pending_inbox_candidates()
        if len(candidates) != 0:
            logger.info("MAX group bot_added rejected: pending channel belongs to another/ambiguous owner chat_id=%s candidates=%s", update.chat_id, len(candidates))
            if initiator:
                await self.client.send_message(initiator, "Чат не подключён: сначала выберите конкретный канал в разделе «Мои каналы» → «Подключить чат».")
            return
        logger.info("MAX group bot_added rejected: no pending inbox channel chat_id=%s", update.chat_id)
        if initiator:
            await self.client.send_message(initiator, "Чат не подключён: сначала выберите канал и нажмите «Подключить чат».")

    async def _bind_inbox_from_callback(self, update: MaxUpdate, channel_id: str) -> None:
        admin_id = self._event_user_id(update)
        if not admin_id or not update.chat_id or not await self.repo.is_admin(admin_id):
            return
        pending_channel = await self.repo.pending_inbox_channel(admin_id)
        if pending_channel != channel_id:
            return
        await self._bind_inbox_chat(admin_id, channel_id, update.chat_id)

    async def _bind_inbox_chat(self, admin_id: str, channel_id: str, chat_id: str) -> None:
        try:
            chat = await self.client.get_chat(chat_id)
            title = chat.get("title") or chat.get("name") if isinstance(chat, dict) else None
        except Exception:
            title = None
        if not await self.repo.bind_inbox_chat(admin_id, channel_id, chat_id, title or ""):
            return
        async with self.session_factory() as db:
            channel = await db.get(Channel, channel_id)
        if not channel:
            return
        await self.client.send_chat_message(
            chat_id,
            f"✅ Чат подключён!\n\n📢 Канал: {channel.title}\n\n"
            "Теперь сюда будут приходить все новые обращения пользователей этого канала.\n\n"
            "Чтобы ответить пользователю: зажмите его сообщение → «Ответить».",
        )
        await self._deliver_inbox_backlog(channel, chat_id)
        await self._render(admin_id, f"channel:{channel_id}", channel_id)

    async def _deliver_inbox_backlog(self, channel: Channel, chat_id: str) -> None:
        rows = await self.repo.channel_inbox_rows(channel.id)
        delivered = 0
        for row in rows:
            if await self.repo.feed_mapping_id(row.id):
                continue
            try:
                attachments = json.loads(row.attachments_json or "[]")
            except (TypeError, ValueError):
                attachments = []
            try:
                feed_id = await self.client.send_feed_message_to_chat(chat_id, _feed_caption(row), attachments)
                await self.repo.save_feed_mapping(feed_id, row.id, row.external_message_id, row.conversation.user_external_id, channel.id, f"inbox:{chat_id}")
                delivered += 1
            except Exception:
                logger.exception("MAX inbox backlog delivery failed channel_id=%s", channel.id)
        logger.warning("MAX inbox backlog channel_id=%s rows=%s delivered=%s", channel.id, len(rows), delivered)

    async def _message(self, update: MaxUpdate):
        message = update.message
        if not message:
            logger.warning("MAX message skipped: empty text")
            return
        forwarded_channel_id = self._forwarded_channel_id(message)
        if not message.text and not forwarded_channel_id and not message.attachments:
            logger.warning("MAX message skipped: no text or forwarded source")
            return
        normalized_text = (message.text or "").strip().lower()
        actor_id = self._event_user_id(update)
        if self._is_start_command(normalized_text):
            logger.warning("MAX start command handled")
            await self._start(update)
            return
        if self._is_admin_command(normalized_text):
            target_id = str(actor_id or update.chat_id or "")
            if not target_id or not await self.repo.is_platform_admin(target_id):
                await self.client.send_message(
                    target_id or update.chat_id,
                    "⛔ Раздел администратора доступен только администраторам.",
                )
                logger.warning("ADMIN_COMMAND_DENIED")
                return
            await self.repo.reset_navigation(target_id)
            await self.repo.clear_support_input(target_id)
            await self._render(target_id, "owner_admin")
            logger.warning("ADMIN_COMMAND_OPENED")
            return
        admin_id = actor_id if await self.repo.is_admin(actor_id) else None
        if admin_id and message.text:
            budget_pending = getattr(self, "_ads_budget_pending", {}).pop(str(admin_id), None)
            if budget_pending:
                object_type, object_id, campaign_id = budget_pending
                try:
                    async with self.session_factory() as db:
                        await set_budget(db, object_type=object_type, object_id=object_id, campaign_id=campaign_id, monthly_budget_limit=message.text.strip())
                        await db.commit()
                    await self.client.send_message(admin_id, "✅ Лимит автопилота сохранён.")
                    await self._render(admin_id, f"ads_budget:{object_type}:{object_id}")
                except ValueError:
                    self._ads_budget_pending[str(admin_id)] = budget_pending
                    suffix = f":{campaign_id}" if campaign_id else ""
                    await self._render(admin_id, f"ads_budget_input:{object_type}:{object_id}{suffix}")
                return
            objective_pending = self._ads_objective_pending.pop(str(admin_id), None)
            if objective_pending:
                object_type, object_id, campaign_id = objective_pending
                try:
                    async with self.session_factory() as db:
                        links = await set_objective(
                            db,
                            object_type=object_type,
                            object_id=object_id,
                            target_cpa=message.text.strip(),
                            currency="RUB",
                            campaign_id=campaign_id,
                        )
                        await db.commit()
                    target_value = links[0].target_cpa if links else message.text.strip()
                    await self.client.send_message(
                        admin_id,
                        f"✅ Цель установлена: {target_value:g} ₽",
                    )
                    await self._render(admin_id, f"ads_objective:{object_type}:{object_id}")
                except ValueError:
                    self._ads_objective_pending[str(admin_id)] = objective_pending
                    await self._render(admin_id, f"ads_objective_input:{object_type}:{object_id}")
                return
            custom_state, pending_bot_id = await self.repo.custom_bot_state(admin_id)
            if (custom_state or "").startswith("broadcast:") and message.text:
                bot_id = custom_state.split(":", 1)[1]
                if await self.repo.bot_owned(admin_id, bot_id):
                    self._broadcast_drafts[str(admin_id)] = message.text.strip()
                    await self._render(admin_id, f"custom_bot_broadcast_confirm:{bot_id}")
                return
            if (custom_state or "").startswith("custom_template:"):
                _, bot_id, kind = custom_state.split(":", 2)
                if await self.repo.update_custom_bot_template(admin_id, bot_id, kind, message.text.strip()):
                    await self.repo.set_custom_bot_state(admin_id, None)
                    await self._render(admin_id, f"custom_bot_template_view:{bot_id}:{kind}")
                return
            # Refresh stores the bot id in the dedicated pending field because
            # Admin.custom_bot_state is intentionally compact.  The previous
            # startswith("refresh:") check could therefore never match and
            # silently routed the submitted token to the generic message
            # flow.
            if custom_state in {"waiting_custom_bot_token", "refresh"}:
                token = message.text.strip()
                try:
                    if not isinstance(self.client, RealMaxClient):
                        raise RuntimeError("custom bot validation requires real MAX")
                    probe = RealMaxClient(token, self.client.base_url)
                    try:
                        profile = await probe.get_me()
                    finally:
                        await probe.close()
                    profile = profile.get("user", profile) if isinstance(profile, dict) else {}
                    if not isinstance(profile, dict) or not (profile.get("user_id") or profile.get("id") or profile.get("bot_id")):
                        raise RuntimeError("invalid bot profile")
                    encrypted = encrypt_token(token)
                    if pending_bot_id:
                        await self.repo.update_custom_bot_token(admin_id, pending_bot_id, encrypted)
                        bot = await self.repo.bot_owned(admin_id, pending_bot_id)
                    else:
                        bot = await self.repo.create_custom_bot(admin_id, profile, encrypted)
                    await self.repo.set_custom_bot_state(admin_id, None)
                    if bot:
                        activation = await self._auto_activate_custom_bot(bot, token, admin_id)
                        if activation == "ACTIVE":
                            await self._render(admin_id, f"custom_bot_active:{bot.id}")
                        elif activation == "START_REQUIRED":
                            await self._render(admin_id, f"custom_bot_found:{bot.id}")
                        else:
                            await self._render(admin_id, "custom_bot_activation_error")
                    return
                except ValueError as exc:
                    if str(exc) == "SUBSCRIPTION_BOT_LIMIT":
                        await self.repo.set_custom_bot_state(admin_id, custom_state, pending_bot_id)
                        await self._render(admin_id, "subscription_limit:bot")
                        return
                    raise
                except Exception:
                    logger.warning("CUSTOM_BOT_TOKEN_REJECTED admin_id=%s", admin_id)
                    await self.repo.set_custom_bot_state(admin_id, custom_state, pending_bot_id)
                    await self._render(admin_id, "custom_bot_error")
                    return
        channel_user_channel_id = await self.repo.self_test_channel(actor_id) if actor_id else None
        reply_id = self._reply_link_id(message.raw or {})
        if update.chat_id and reply_id and await self.repo.channel_by_inbox_chat(update.chat_id):
            await self._handle_inbox_reply(update, message, reply_id)
            return
        if update.chat_id and settings.max_support_chat_id and str(update.chat_id) == str(settings.max_support_chat_id):
            if reply_id:
                await self._handle_support_reply(update, message, reply_id)
            else:
                logger.info("SUPPORT_CHAT_MESSAGE_IGNORED_NO_REPLY")
            return
        if admin_id and reply_id:
            mapping = await self.repo.mapping_for_reply(admin_id, reply_id)
            if mapping and (message.text or message.attachments):
                try:
                    source_is_self_test = await self.repo.is_self_test_message(mapping.source_message_id)
                    if not (source_is_self_test and mapping.source_user_id == admin_id):
                        await self.client.send_feed_message(mapping.source_user_id, message.text or "", message.attachments or [])
                    saved = await self.repo.save_native_reply(admin_id, mapping, message.text or "", message.attachments or [])
                    if saved:
                        logger.warning("MAX native reply routed via mapping")
                    if source_is_self_test and mapping.source_user_id == admin_id:
                        logger.warning(
                            "SELF_TEST_REPLY_OK channel_id=%s source_message_id=%s source_user_id=%s admin_feed_message_id=%s",
                            mapping.channel_id, mapping.source_message_id, mapping.source_user_id, mapping.admin_feed_message_id,
                        )
                except Exception:
                    logger.exception("MAX native reply delivery failed")
            return
        # A connected inbox chat is an administrative transport. Ordinary
        # messages in it must not be mistaken for new public-link messages;
        # native Reply above is the only message path handled here.
        if update.chat_id and await self.repo.channel_by_inbox_chat(update.chat_id):
            return
        if admin_id and await self._handle_support_input(admin_id, message, update.user_name or message.sender_name):
            return
        is_self_test = bool(channel_user_channel_id and not forwarded_channel_id)
        if is_self_test:
            channel = await self.repo.channel_for_admin(actor_id, channel_user_channel_id)
            if not channel or not channel.accepting:
                return
            user_id = actor_id
        elif admin_id and not forwarded_channel_id:
            pending = await self.repo.pending_template(admin_id)
            if pending:
                if not (message.text or "").strip():
                    await self._render(admin_id, f"template_prompt:{pending[0]}:{pending[1]}", pending[0])
                    return
                await self.repo.save_template(admin_id, message.text.strip())
                await self._render(admin_id, f"template_saved:{pending[0]}:{pending[1]}", pending[0])
                return
            # A plain administrator message is never routed to a last/active user.
            return
        # A forwarded channel post is associated with the administrator who sent it.
        if forwarded_channel_id and admin_id:
            data = await self.client.validate_forwarded_post(message)
            data["external_id"] = forwarded_channel_id
            try:
                channel_id = await self.repo.create_channel(admin_id, data)
            except ValueError as exc:
                if str(exc) == "SUBSCRIPTION_CHANNEL_LIMIT":
                    await self._render(admin_id, "subscription_limit:channel")
                    return
                raise
            await self.repo.activate_pending_free_trial(str(admin_id), str(channel_id))
            # A forwarded post is already the newest MAX message.  Recreate
            # the service panel so the add confirmation is also newest and
            # visible immediately instead of editing a panel far above it.
            await self._render(admin_id, f"channel_added:{channel_id}", channel_id, force_new=True)
            return

        if not is_self_test:
            channel = await self._channel_for_user(update)
        if not channel or not channel.accepting:
            return
        if await self.repo.has_external_message(message.external_message_id or None):
            logger.info("MAX duplicate message ignored")
            return
        user_id = admin_id or message.sender_id
        logger.warning(
            "MAX parsed message has_text=%s attachments_count=%s attachment_types=%s",
            bool(message.text and message.text.strip()),
            len(message.attachments or []),
            [a.get("type") for a in (message.attachments or []) if isinstance(a, dict)],
        )
        lock_key = channel.id
        message_lock = self._message_locks.setdefault(lock_key, asyncio.Lock())
        async with message_lock:
            conversation_id, is_new_message, stored_message_id = await self.repo.create_incoming_once(
                channel,
                user_id,
                update.user_name or message.sender_name,
                message.text,
                message.external_message_id or None,
                update.user_avatar_url or message.avatar_url,
                message.attachments,
                is_self_test=is_self_test,
            )
        if not is_new_message:
            logger.info("MAX duplicate incoming message ignored")
            return
        owner_external_id = await self.repo.get_channel_owner_external(channel.id)
        if owner_external_id:
            if channel.inbox_chat_id:
                await self._deliver_inbox_message(owner_external_id, channel, stored_message_id)
            else:
                await self._deliver_native_feed_message(owner_external_id, channel.id, stored_message_id)
        logger.warning("MAX outbound kind=ack text_hash=%s", hashlib.sha256((channel.sent_text or "").encode()).hexdigest()[:12])
        await self.client.send_message(user_id, normalize_channel_sent(channel.sent_text))
        logger.info("MAX user message stored conversation_id=%s", conversation_id)

    async def _deliver_inbox_message(self, admin_id: str, channel: Channel, message_id: str) -> None:
        if not channel.inbox_chat_id or await self.repo.feed_mapping_id(message_id):
            return
        async with self.session_factory() as db:
            row = await db.scalar(select(Message).options(selectinload(Message.conversation)).where(Message.id == message_id))
        if not row:
            return
        try:
            attachments = json.loads(row.attachments_json or "[]")
        except (TypeError, ValueError):
            attachments = []
        try:
            inbox_message_id = await self.client.send_feed_message_to_chat(channel.inbox_chat_id, _feed_caption(row), attachments)
            await self.repo.save_feed_mapping(
                inbox_message_id, row.id, row.external_message_id,
                row.conversation.user_external_id, channel.id, f"inbox:{channel.inbox_chat_id}",
            )
            logger.warning("MAX inbox message delivered channel_id=%s chat_id=%s", channel.id, channel.inbox_chat_id)
        except Exception:
            logger.exception("MAX inbox message delivery failed channel_id=%s chat_id=%s", channel.id, channel.inbox_chat_id)

    async def send_admin_reply(self, conversation_id: str, text: str):
        async with self.session_factory() as db:
            conversation = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
            if not conversation:
                raise LookupError("conversation not found")
            db.add(Message(conversation_id=conversation.id, direction="out", text=text))
            await db.commit()
            await self.client.send_to_channel_user(conversation.user_external_id, text)

    async def _channel_for_user(self, update: MaxUpdate):
        user_id = self._event_user_id(update) or (update.message.sender_id if update.message else None)
        channel_id = self._user_channels.get(user_id or "")
        if channel_id:
            async with self.session_factory() as db:
                return await db.get(Channel, channel_id)
        recipient_id = update.message.recipient_id if update.message else None
        if recipient_id:
            return await self._channel_by_slug(recipient_id)
        if user_id:
            async with self.session_factory() as db:
                conversation = await db.scalar(select(Conversation).where(Conversation.user_external_id == user_id).order_by(Conversation.last_message_at.desc()))
                if conversation:
                    return await db.get(Channel, conversation.channel_id)
        return None

    @staticmethod
    def _event_user_id(update: MaxUpdate) -> str | None:
        """Extract the dialog target from all documented and observed MAX shapes."""
        if update.user_id and str(update.user_id) not in {"0", ""}:
            return str(update.user_id)
        raw = update.raw or {}
        for candidate in (
            raw.get("user"),
            raw.get("sender"),
            (raw.get("callback") or {}).get("user") if isinstance(raw.get("callback"), dict) else None,
            (raw.get("message") or {}).get("sender") if isinstance(raw.get("message"), dict) else None,
        ):
            if isinstance(candidate, dict):
                value = candidate.get("user_id") or candidate.get("id")
                if value not in (None, 0, "0", ""):
                    return str(value)
        if update.chat_id and str(update.chat_id) not in {"0", ""}:
            return str(update.chat_id)
        value = raw.get("chat_id")
        return str(value) if value not in (None, 0, "0", "") else None

    async def _channel_by_slug(self, slug: str):
        async with self.session_factory() as db:
            return await db.scalar(select(Channel).where(Channel.public_slug == slug))

    @staticmethod
    def _action_parts(action: str) -> tuple[str, str | None]:
        bits = action.split(":")
        if bits[0] == "channel":
            return action, bits[1] if len(bits) > 1 else None
        if bits[0] == "settings":
            return action, bits[1] if len(bits) > 1 else None
        if bits[0] == "receiving":
            return f"channel:{bits[1]}", bits[1]
        if bits[0] == "template":
            return "template", bits[1] if len(bits) > 1 else None
        if bits[0] == "template_view":
            return "template_view", bits[1] if len(bits) > 1 else None
        if bits[0] in {"cancel_template", "reset_template"}:
            return bits[0], bits[1] if len(bits) > 1 else None
        return bits[0], None

    @staticmethod
    def _start_payload(update: MaxUpdate) -> str | None:
        candidates = MaxBotRuntime._start_payload_candidates(update)
        return candidates[0] if candidates else None

    @staticmethod
    def _start_payload_candidates(update: MaxUpdate) -> list[str]:
        raw = update.raw or {}
        # MAX bot_started normally carries the deep-link value in `payload`,
        # but production events have also arrived with one or more wrapper
        # objects around it. Collect only documented payload-like keys; never
        # guess from arbitrary IDs or message fields.
        values: list[str] = []

        def append(value: object) -> None:
            if not isinstance(value, str):
                return
            value = unquote(value).strip()
            if not value or value in values:
                return
            # iOS universal-link delivery can wrap the original MAX start
            # parameter in the URL or a query-shaped payload. Extract only
            # the documented start fields; never treat an arbitrary URL as a
            # traffic token.
            if value.startswith(("http://", "https://")):
                query = parse_qs(urlsplit(value).query)
                for key in ("start", "startapp", "start_param"):
                    for candidate in query.get(key, []):
                        append(candidate)
                return
            if value.startswith(("?start=", "?startapp=", "?start_param=", "start=", "startapp=", "start_param=")):
                query = parse_qs(value[1:] if value.startswith("?") else value)
                for key in ("start", "startapp", "start_param"):
                    for candidate in query.get(key, []):
                        append(candidate)
                return
            if value.startswith("/start"):
                rest = value[6:].strip()
                if rest and not rest.startswith("?"):
                    append(rest)
                return
            if value not in values:
                values.append(value)

        def collect(value: object, depth: int = 0) -> None:
            if depth > 4 or value in (None, ""):
                return
            if isinstance(value, str):
                append(value)
                return
            if not isinstance(value, dict):
                return
            for key in ("payload", "start", "start_payload", "start_param", "startapp", "value", "data"):
                collect(value.get(key), depth + 1)

        collect(raw)
        if update.message and update.message.text:
            command_text = update.message.text.strip()
            if command_text.lower().startswith("/start"):
                rest = command_text[6:].strip()
                if rest.startswith("@"):
                    rest = rest.split(maxsplit=1)[1] if " " in rest else ""
                if rest.startswith("?"):
                    append(parse_qs(rest[1:]).get("start", [None])[0])
                elif rest.startswith("="):
                    append(rest[1:])
                elif rest:
                    append(rest.split(maxsplit=1)[0])
        return values

    @staticmethod
    def _is_start_command(text: str) -> bool:
        if not text.startswith("/start"):
            return False
        return len(text) == 6 or text[6] in " @?=" or text[6].isspace()

    @staticmethod
    def _is_admin_command(text: str) -> bool:
        """Recognize the admin command without consuming start payloads."""
        command = (text or "").split(maxsplit=1)[0]
        name = command.split("@", 1)[0]
        return name == "/admin"

    @staticmethod
    def _reply_link_id(raw: dict) -> str | None:
        """Extract only an explicitly reply-shaped linked message id.

        MAX payload is logged by key/type first; this parser accepts the
        observed/documented reply variants without using sender or last-user
        heuristics.
        """
        link = raw.get("link") if isinstance(raw, dict) else None
        if not isinstance(link, dict):
            return None
        import logging
        logger.warning("MAX reply link type=%s keys=%s", link.get("type"), sorted(link.keys()))
        link_type = str(link.get("type") or "").lower()
        replyish = any(token in link_type for token in ("reply", "quote")) or any(
            key in link for key in ("reply_to", "reply_to_message", "replied_message", "linked_message")
        )
        if not replyish:
            return None

        def find(value):
            if not isinstance(value, dict):
                return None
            for key in ("reply_to_message_id", "replied_message_id", "linked_message_id", "message_id", "mid", "id"):
                candidate = value.get(key)
                if candidate not in (None, "", 0, "0") and not isinstance(candidate, dict):
                    return str(candidate)
            for key in ("reply_to", "reply_to_message", "replied_message", "linked_message", "message", "quote"):
                candidate = find(value.get(key))
                if candidate:
                    return candidate
            return None

        result = find(link)
        logger.warning("MAX reply linked_message_id_present=%s", bool(result))
        return result

    @staticmethod
    def _forwarded_channel_id(message: MaxMessage) -> str | None:
        raw = message.raw or {}
        link = raw.get("link") or {}
        if isinstance(link, dict):
            recipient = link.get("recipient") if isinstance(link.get("recipient"), dict) else {}
            chat_id = link.get("chat_id") or recipient.get("chat_id") or link.get("chat") and link.get("chat", {}).get("chat_id")
            if chat_id is not None:
                return str(chat_id)
        # Keep compatibility with older fixtures, but `link` is the documented path.
        forwarded = raw.get("forward") or raw.get("forwarded_message")
        if isinstance(forwarded, dict):
            return str(forwarded.get("chat_id") or forwarded.get("recipient", {}).get("chat_id") or "") or None
        return None
