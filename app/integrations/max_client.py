from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import ssl
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


@dataclass
class MaxMessage:
    external_message_id: str
    sender_id: str
    sender_name: str
    text: str
    channel_id: str | None = None
    recipient_id: str | None = None
    is_channel: bool = False
    raw: dict[str, Any] | None = None
    avatar_url: str | None = None
    attachments: list[dict[str, Any]] | None = None


@dataclass
class MaxUpdate:
    update_type: str
    chat_id: str | None = None
    user_id: str | None = None
    user_name: str = "Пользователь"
    message: MaxMessage | None = None
    callback_id: str | None = None
    button_payload: str | None = None
    raw: dict[str, Any] | None = None
    user_avatar_url: str | None = None
    bot_connection_id: str | None = None


class MaxClient(ABC):
    @abstractmethod
    async def edit_message(self, conversation_id: str, message_id: str, text: str,
                           buttons: list[list[dict]], attachments: list[dict[str, Any]] | None = None,
                           *, disable_link_preview: bool | None = None) -> str: ...

    @abstractmethod
    async def send_message(self, conversation_id: str, text: str,
                           buttons: list[list[dict]] | None = None,
                           attachments: list[dict[str, Any]] | None = None,
                           *, format: str | None = None,
                           disable_link_preview: bool | None = None) -> str: ...

    @abstractmethod
    async def validate_forwarded_post(self, message: MaxMessage) -> dict: ...

    @abstractmethod
    async def send_to_channel_user(self, user_id: str, text: str) -> bool: ...

    async def send_feed_message(self, user_id: str, text: str, attachments: list[dict[str, Any]] | None = None) -> str:
        return await self.send_message(user_id, text)

    async def send_chat_message(self, chat_id: str, text: str, attachments: list[dict[str, Any]] | None = None,
                                buttons: list[list[dict]] | None = None) -> str:
        """Send a message to a group chat; clients without chat support use their normal target."""
        return await self.send_message(chat_id, text, buttons)

    async def send_feed_message_to_chat(self, chat_id: str, text: str,
                                        attachments: list[dict[str, Any]] | None = None) -> str:
        return await self.send_chat_message(chat_id, text, attachments)

    async def delete_message(self, message_id: str) -> bool:
        return False

    async def leave_chat(self, chat_id: str) -> bool:
        """Leave a MAX chat/channel without deleting the MAX object."""
        return False

    async def send_image_to_channel_user(self, user_id: str, image_url: str, text: str = "") -> bool:
        raise NotImplementedError

    async def answer_callback(self, callback_id: str, message: dict[str, Any] | None = None,
                              notification: str | None = None) -> bool:
        return True

    async def updates(self, marker: int | None = None) -> tuple[list[MaxUpdate], int | None]:
        return [], marker

    async def get_member(self, chat_id: str, user_id: str) -> dict[str, Any] | None:
        return None

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        """Return chat metadata when the client supports the official endpoint."""
        return {}

    async def get_me(self) -> dict[str, Any]:
        return {}

    async def get_admins(self, chat_id: str) -> dict[str, Any]:
        return {}


class StubMaxClient(MaxClient):
    def __init__(self, fail_edits: bool = False):
        self.messages: dict[tuple[str, str], tuple[str, list[list[dict]]]] = {}
        self.seq = 0
        self.fail_edits = fail_edits
        self.deliveries: list[tuple[str, str]] = []
        self.receiving = True

    async def edit_message(self, conversation_id, message_id, text, buttons, attachments=None,
                           *, disable_link_preview=None):
        if self.fail_edits:
            raise RuntimeError("stub edit failure")
        self.messages[(conversation_id, message_id)] = (text, buttons)
        return message_id

    async def send_message(self, conversation_id, text, buttons=None, attachments=None,
                           *, format=None, disable_link_preview=None):
        self.seq += 1
        message_id = str(self.seq)
        self.messages[(conversation_id, message_id)] = (text, buttons or [])
        return message_id

    async def validate_forwarded_post(self, message):
        return {"external_id": message.channel_id or "stub-channel", "title": "Новый канал"}

    async def send_to_channel_user(self, user_id, text):
        self.deliveries.append((user_id, text))
        return True

    async def send_image_to_channel_user(self, user_id, image_url, text=""):
        self.deliveries.append((user_id, text or "[Фото]"))
        return True

    async def leave_chat(self, chat_id: str) -> bool:
        return True

    async def get_member(self, chat_id: str, user_id: str) -> dict[str, Any] | None:
        return None

    async def dispatch_callback(self, router, admin_id: str, action: str, message_id: str | None = None):
        return await router.render(admin_id, action, message_id)


class MaxApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"MAX API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class RealMaxClient(MaxClient):
    """MAX REST client using the documented platform-api2.max.ru endpoints."""

    def __init__(self, token: str | None = None, base_url: str | None = None, timeout: float = 30.0):
        self._token = token or settings.max_bot_token
        if not self._token:
            raise RuntimeError("MAX_BOT_TOKEN is required")
        self.base_url = (base_url or settings.max_api_base_url).rstrip("/")
        self.timeout = timeout
        ca_bundle = Path("/etc/ssl/certs/ca-certificates.crt")
        verify = ssl.create_default_context(cafile=str(ca_bundle)) if ca_bundle.exists() else ssl.create_default_context()
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            verify=verify,
            headers={"Authorization": self._token, "Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        started = time.monotonic()
        response = await self._client.request(method, path, **kwargs)
        import logging
        logging.getLogger(__name__).warning(
            "MAX API %s %s -> %s duration_ms=%.0f",
            method,
            path,
            response.status_code,
            (time.monotonic() - started) * 1000,
        )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:500]
            raise MaxApiError(response.status_code, str(detail))
        if not response.content:
            return {}
        return response.json()

    @staticmethod
    def _attachments(buttons: list[list[dict]] | None) -> list[dict]:
        if not buttons:
            return []
        max_buttons = []
        for row in buttons:
            max_row = []
            for button in row:
                if button.get("type") == "callback":
                    max_row.append({
                        "type": "callback",
                        "text": button["text"],
                        "payload": button.get("payload", ""),
                    })
                elif button.get("callback_data") is not None:
                    max_row.append({
                        "type": "callback",
                        "text": button["text"],
                        "payload": button["callback_data"],
                    })
                else:
                    max_row.append(button)
            max_buttons.append(max_row)
        return [{"type": "inline_keyboard", "payload": {"buttons": max_buttons}}]

    async def send_message(self, conversation_id, text, buttons=None, attachments=None,
                           *, format=None, disable_link_preview=None):
        data = {"text": text}
        if format is not None:
            data["format"] = format
        elif "**" in text or "[Предложка]" in text:
            data["format"] = "markdown"
        elif "<b>" in text or "<strong>" in text:
            data["format"] = "html"
        if disable_link_preview is not None:
            data["disable_link_preview"] = disable_link_preview
        elif "vkvideo.ru" in text:
            data["disable_link_preview"] = True
        merged_attachments = list(attachments or []) + self._attachments(buttons)
        if merged_attachments:
            data["attachments"] = merged_attachments
        import logging
        logging.getLogger(__name__).warning(
            "POST_MESSAGE text=%r attachments_types=%r",
            text,
            [item.get("type") for item in merged_attachments],
        )
        result = await self._request("POST", "/messages", params={"user_id": conversation_id}, json=data)
        message = result.get("message") if isinstance(result, dict) else None
        body = message.get("body") if isinstance(message, dict) else None
        message_id = (body or {}).get("mid") if isinstance(body, dict) else None
        message_id = message_id or (message or {}).get("mid") if isinstance(message, dict) else message_id
        message_id = message_id or result.get("message_id") or result.get("mid")
        if not message_id:
            raise MaxApiError(502, "MAX send response has no message id")
        return str(message_id)

    async def send_chat_message(self, chat_id, text, attachments=None, buttons=None):
        data: dict[str, Any] = {"text": text}
        if "**" in text or "[Предложка]" in text:
            data["format"] = "markdown"
        elif "<b>" in text or "<strong>" in text:
            data["format"] = "html"
        merged = list(attachments or [])
        merged.extend(self._attachments(buttons))
        if merged:
            data["attachments"] = merged
        import logging
        logging.getLogger(__name__).warning(
            "POST_CHAT_MESSAGE chat_id=%s text=%r attachments_types=%r",
            chat_id, text, [item.get("type") for item in merged if isinstance(item, dict)],
        )
        result = await self._request("POST", "/messages", params={"chat_id": chat_id}, json=data)
        message = result.get("message") if isinstance(result, dict) else None
        body = message.get("body") if isinstance(message, dict) else None
        message_id = (body or {}).get("mid") if isinstance(body, dict) else None
        message_id = message_id or (message or {}).get("mid") if isinstance(message, dict) else message_id
        message_id = message_id or result.get("message_id") or result.get("mid")
        if not message_id:
            raise MaxApiError(502, "MAX chat send response has no message id")
        return str(message_id)

    async def send_feed_message(self, conversation_id, text, attachments=None):
        data: dict[str, Any] = {}
        if text:
            data["text"] = text
        if attachments:
            data["attachments"] = attachments
        import logging
        logging.getLogger(__name__).warning(
            "POST_FEED_MESSAGE text=%r attachments_types=%r",
            text,
            [item.get("type") for item in (attachments or []) if isinstance(item, dict)],
        )
        result = await self._request("POST", "/messages", params={"user_id": conversation_id}, json=data)
        message = result.get("message") if isinstance(result, dict) else None
        body = message.get("body") if isinstance(message, dict) else None
        message_id = (body or {}).get("mid") if isinstance(body, dict) else None
        message_id = message_id or (message or {}).get("mid") if isinstance(message, dict) else message_id
        message_id = message_id or result.get("message_id") or result.get("mid")
        if not message_id:
            raise MaxApiError(502, "MAX feed response has no message id")
        return str(message_id)

    async def send_feed_message_to_chat(self, chat_id, text, attachments=None):
        return await self.send_chat_message(chat_id, text, attachments)

    async def delete_message(self, message_id: str) -> bool:
        result = await self._request("DELETE", "/messages", params={"message_id": message_id})
        return not (isinstance(result, dict) and result.get("success") is False)

    async def leave_chat(self, chat_id: str) -> bool:
        try:
            await self._request("DELETE", f"/chats/{chat_id}/members/me")
        except MaxApiError as exc:
            # Already absent is equivalent to having left; keep deletion
            # idempotent when MAX has processed a previous attempt.
            if exc.status_code == 404:
                return True
            raise
        return True

    async def edit_message(self, conversation_id, message_id, text, buttons, attachments=None,
                           *, disable_link_preview=None):
        import logging
        data = {"text": text}
        if "**" in text or "[Предложка]" in text:
            data["format"] = "markdown"
        elif "<b>" in text or "<strong>" in text:
            data["format"] = "html"
        # MAX keeps the old inline keyboard when `attachments` is omitted.
        # Send an explicit empty list when the new screen has no buttons, so
        # a previous HOME keyboard cannot remain attached to the feed header.
        merged_attachments = list(attachments or []) + self._attachments(buttons)
        data["attachments"] = merged_attachments
        if disable_link_preview is not None:
            data["disable_link_preview"] = disable_link_preview
        elif "vkvideo.ru" in text:
            data["disable_link_preview"] = True
        logging.getLogger(__name__).warning(
            "MAX PUT /messages payload=%s",
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        )
        result = await self._request("PUT", "/messages", params={"message_id": message_id}, json=data)
        if isinstance(result, dict) and result.get("success") is False:
            raise MaxApiError(409, "MAX rejected message edit")
        return str(result.get("message_id", message_id))

    async def answer_callback(self, callback_id, message=None, notification=None):
        # A callback must be acknowledged through the dedicated MAX API
        # endpoint.  Returning locally leaves the callback pending and MAX
        # retries it, which makes button presses look slow and duplicated.
        # MAX rejects an empty callback body (400). For navigation the
        # runtime edits the persisted message first, then this short
        # acknowledgement confirms the already-updated screen without
        # creating a second message version.
        payload = {"message": message} if message else {"notification": notification or "Готово"}
        result = await self._request(
            "POST",
            "/answers",
            params={"callback_id": callback_id},
            json=payload,
        )
        return bool(result.get("success", True)) if isinstance(result, dict) else True

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/chats/{chat_id}")

    async def get_me(self) -> dict[str, Any]:
        """Validate a bot token and return the bot profile."""
        return await self._request("GET", "/me")

    async def get_membership(self, chat_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/chats/{chat_id}/members/me")

    async def get_member(self, chat_id: str, user_id: str) -> dict[str, Any] | None:
        """Check one user using the documented user_ids filter.

        MAX returns a members array; an empty array is a negative membership
        result. The bot must be an administrator of the channel/chat.
        """
        result = await self._request(
            "GET",
            f"/chats/{chat_id}/members",
            params={"user_ids": [str(user_id)]},
        )
        members = result.get("members", []) if isinstance(result, dict) else []
        if not isinstance(members, list):
            return None
        for member in members:
            if isinstance(member, dict) and str(member.get("user_id") or member.get("id")) == str(user_id):
                return member
        return None

    async def get_admins(self, chat_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/chats/{chat_id}/members/admins")

    async def subscribe_webhook(self, url: str, update_types: list[str], secret: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url, "update_types": update_types}
        if secret:
            payload["secret"] = secret
        return await self._request("POST", "/subscriptions", json=payload)

    async def webhook_subscriptions(self) -> dict[str, Any]:
        return await self._request("GET", "/subscriptions")

    async def unsubscribe_webhook(self, url: str | None = None) -> dict[str, Any]:
        params = {"url": url} if url else None
        return await self._request("DELETE", "/subscriptions", params=params)

    async def validate_forwarded_post(self, message: MaxMessage):
        if not message.channel_id:
            raise MaxApiError(400, "forwarded post has no channel_id")
        chat = await self.get_chat(message.channel_id)
        membership = await self.get_membership(message.channel_id)
        member = membership.get("member", membership) if isinstance(membership, dict) else {}
        permissions = set(member.get("permissions") or []) if isinstance(member, dict) else set()
        is_admin = bool(member.get("is_admin") or member.get("is_owner")) if isinstance(member, dict) else False
        if not is_admin or "write" not in permissions:
            raise MaxApiError(403, "MAXIBOT must be a channel administrator with write permission")
        return {
            "external_id": message.channel_id,
            "title": chat.get("title") or chat.get("name") or "Канал MAX",
            "avatar_url": self._image_url(chat.get("icon")),
            "membership": membership,
            "is_channel": bool(chat.get("is_channel", message.is_channel)),
            "chat": chat,
        }

    @staticmethod
    def _image_url(value: Any) -> str | None:
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value
        if isinstance(value, dict):
            for key in ("full_avatar_url", "avatar_url", "url"):
                item = value.get(key)
                if isinstance(item, str) and item.startswith(("https://", "http://")):
                    return item
            for key in ("image", "photo", "icon"):
                result = RealMaxClient._image_url(value.get(key))
                if result:
                    return result
        return None

    async def send_to_channel_user(self, user_id, text):
        await self.send_message(user_id, text)
        return True

    async def send_image_to_channel_user(self, user_id, image_url, text=""):
        data = {
            "text": text,
            "attachments": [{"type": "image", "payload": {"url": image_url}}],
        }
        await self._request("POST", "/messages", params={"user_id": user_id}, json=data)
        return True

    async def updates(self, marker: int | None = None):
        params: dict[str, Any] = {"timeout": 25}
        if marker is not None:
            params["marker"] = marker
        result = await self._request("GET", "/updates", params=params)
        self._log_raw_updates(result)
        updates = [self._parse_update(item) for item in result.get("updates", [])]
        return updates, result.get("marker")

    @staticmethod
    def _safe_id(value: Any) -> str:
        value = str(value or "")
        return f"…{value[-8:]}" if value else "none"

    @classmethod
    def _log_raw_updates(cls, result: dict[str, Any]) -> None:
        """Log attachment shape before any parser/runtime filtering.

        Values are deliberately reduced to presence, type, counts and short
        ID suffixes; message text, URLs, tokens and user data are not logged.
        """
        import logging
        logger = logging.getLogger(__name__)
        for item in result.get("updates", []) if isinstance(result, dict) else []:
            message = item.get("message") or item.get("body") or {}
            if not isinstance(message, dict):
                message = {}
            body = message.get("body")
            if not isinstance(body, dict):
                body = {}
            sender = message.get("sender") or item.get("sender") or {}
            if not isinstance(sender, dict):
                sender = {}
            attachments = body.get("attachments")
            if attachments is None:
                attachments = message.get("attachments")
            if isinstance(attachments, dict):
                attachments = [attachments]
            if not isinstance(attachments, list):
                attachments = []
            logger.warning(
                "RAW UPDATE type=%s timestamp=%s message_id=%s sender_id=%s body=%s text=%s attachments_count=%s attachment_types=%s",
                item.get("update_type"),
                bool(item.get("timestamp") or message.get("timestamp")),
                cls._safe_id(message.get("mid") or message.get("id") or body.get("mid")),
                cls._safe_id(sender.get("user_id") or sender.get("id")),
                bool(message.get("body")),
                "null" if body.get("text") is None and message.get("text") is None else "present",
                len(attachments),
                [a.get("type") for a in attachments if isinstance(a, dict)],
            )

    def _parse_update(self, item: dict[str, Any]) -> MaxUpdate:
        update_type = item.get("update_type", "")
        message_data = item.get("message") or item.get("body")
        message_sender = (message_data or {}).get("sender") or {}
        callback_data = item.get("callback") or {}
        callback_user = callback_data.get("user") or callback_data.get("sender") or {}
        if update_type == "message_callback":
            # In MAX callback updates the actor is carried by callback.user.
            user = callback_user or item.get("user") or item.get("sender") or message_sender or {}
        else:
            user = item.get("user") or item.get("sender") or message_sender or callback_user or {}
        message = None
        recipient = {}
        if message_data:
            recipient = message_data.get("recipient") or {}
            body_value = message_data.get("body")
            body = body_value if isinstance(body_value, dict) else {}
            text = body.get("text") or message_data.get("text") or message_data.get("message") or ""
            if isinstance(body_value, str):
                text = body_value
            attachments = body.get("attachments") or message_data.get("attachments") or []
            if isinstance(attachments, dict):
                attachments = [attachments]
            if not attachments:
                import logging
                logging.getLogger(__name__).warning(
                    "MAX attachment shape message_keys=%s body_keys=%s",
                    sorted(message_data.keys()),
                    sorted(body.keys()),
                )
            link = message_data.get("link") or {}
            linked_recipient = link.get("recipient") if isinstance(link, dict) else {}
            source_chat_id = (link.get("chat_id") if isinstance(link, dict) else None) or (linked_recipient or {}).get("chat_id")
            message = MaxMessage(
                external_message_id=str(
                    message_data.get("mid")
                    or message_data.get("id")
                    or body.get("mid")
                    or body.get("message_id")
                    or ""
                ),
                sender_id=str((message_data.get("sender") or user).get("user_id", "")),
                sender_name=(message_data.get("sender") or user).get("name", "Пользователь"),
                avatar_url=self._image_url(message_data.get("sender") or user),
                text=str(text),
                channel_id=str(source_chat_id or recipient.get("chat_id")) if (source_chat_id or recipient.get("chat_id")) is not None else None,
                recipient_id=str(recipient.get("user_id")) if recipient.get("user_id") is not None else None,
                is_channel=bool(item.get("is_channel", False)),
                raw=message_data,
                attachments=attachments if isinstance(attachments, list) else [],
            )
        raw_chat_id = item.get("chat_id")
        if raw_chat_id in (None, 0, "0", ""):
            raw_chat_id = recipient.get("chat_id") or None
        if raw_chat_id in (0, "0", ""):
            raw_chat_id = None
        raw_user_id = user.get("user_id") or user.get("id") or item.get("user_id") or item.get("userId") or None
        if raw_user_id in (0, "0", ""):
            raw_user_id = None
        return MaxUpdate(
            update_type=update_type,
            chat_id=str(raw_chat_id) if raw_chat_id is not None else None,
            user_id=str(raw_user_id) if raw_user_id is not None else None,
            user_name=user.get("name", "Пользователь"),
            message=message,
            callback_id=(item.get("callback", {}) or {}).get("callback_id") or item.get("callback_id"),
            button_payload=(item.get("callback", {}) or {}).get("payload") or item.get("payload"),
            raw=item,
            user_avatar_url=self._image_url(user),
        )
