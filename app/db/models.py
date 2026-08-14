from __future__ import annotations

import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text, Integer, UniqueConstraint, Sequence
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
def uid(): return str(uuid.uuid4())
def now(): return datetime.now(timezone.utc)
class Admin(Base):
    __tablename__="admins"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    external_id: Mapped[str]=mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    service_message_id: Mapped[Optional[str]]=mapped_column(String(128))
    messages_header_message_id: Mapped[Optional[str]]=mapped_column(String(128))
    messages_controls_message_id: Mapped[Optional[str]]=mapped_column(String(128))
    pending_template_channel_id: Mapped[Optional[str]]=mapped_column(String(36))
    pending_template_type: Mapped[Optional[str]]=mapped_column(String(16))
    pending_reply_conversation_id: Mapped[Optional[str]]=mapped_column(String(36))
    active_feed_channel_id: Mapped[Optional[str]]=mapped_column(String(36))
    feed_session_id: Mapped[Optional[str]]=mapped_column(String(36))
    self_test_mode: Mapped[bool]=mapped_column(Boolean, default=False, nullable=False)
    self_test_channel_id: Mapped[Optional[str]]=mapped_column(String(36))
    pending_inbox_channel_id: Mapped[Optional[str]]=mapped_column(String(36))
    pending_traffic_channel_id: Mapped[Optional[str]]=mapped_column(String(36))
    support_input_mode: Mapped[bool]=mapped_column(Boolean, default=False, nullable=False)
    support_input_ticket_id: Mapped[Optional[str]]=mapped_column(String(36))
    custom_bot_state: Mapped[Optional[str]]=mapped_column(String(32))
    pending_custom_bot_id: Mapped[Optional[str]]=mapped_column(String(36))
    channels=relationship("Channel", back_populates="admin", cascade="all, delete-orphan")

class ChannelSlugRegistry(Base):
    __tablename__="channel_slug_registry"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    max_channel_external_id: Mapped[str]=mapped_column(String(128), unique=True, index=True)
    public_slug: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    last_connected_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    disconnected_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))

class Channel(Base):
    __tablename__="channels"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    admin_id: Mapped[str]=mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str]=mapped_column(String(128), index=True)
    title: Mapped[str]=mapped_column(String(255))
    avatar_url: Mapped[Optional[str]]=mapped_column(String(2048))
    accepting: Mapped[bool]=mapped_column(Boolean, default=True, nullable=False)
    auto_approve_enabled: Mapped[bool]=mapped_column(Boolean, default=False, nullable=False)
    public_slug: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    inbox_chat_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)
    inbox_chat_title: Mapped[Optional[str]]=mapped_column(String(255))
    inbox_chat_connected_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    communication_mode: Mapped[str]=mapped_column(String(20), default="SYSTEM_INBOX", nullable=False)
    bot_connection_id: Mapped[Optional[str]]=mapped_column(String(36), index=True)
    max_owner_user_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)
    max_chat_metadata_json: Mapped[Optional[str]]=mapped_column(Text)
    connected_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    welcome_text: Mapped[str]=mapped_column(Text, default="📩 Есть новость или интересный материал?\nПришлите сюда текст, фото или видео — редакция канала всё посмотрит.")
    sent_text: Mapped[str]=mapped_column(Text, default="✅ Спасибо! Материал получен и передан редакции на рассмотрение.")
    blocked_users=relationship("BlockedUser", back_populates="channel", cascade="all, delete-orphan")
    admin=relationship("Admin", back_populates="channels")
    conversations=relationship("Conversation", back_populates="channel", cascade="all, delete-orphan")

class LandingPage(Base):
    __tablename__ = "landing_pages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(2048))
    cta_text: Mapped[str] = mapped_column(String(255), default="Перейти в канал")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class TrafficProfile(Base):
    __tablename__ = "traffic_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), unique=True, index=True)
    yandex_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    yandex_counter_id: Mapped[Optional[str]] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    target_url: Mapped[Optional[str]] = mapped_column(String(2048))
    channel_mode: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    attribution_window_hours: Mapped[int] = mapped_column(Integer, default=72, nullable=False)
    conversion_policy: Mapped[str] = mapped_column(String(24), default="CONSERVATIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class TrafficVisit(Base):
    __tablename__ = "traffic_visits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    visit_token: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    # Opaque value carried in the signed MAX startapp/start_param payload.
    startapp_token: Mapped[Optional[str]] = mapped_column(String(96), unique=True, index=True)
    landing_page_id: Mapped[str] = mapped_column(ForeignKey("landing_pages.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    utm_source: Mapped[Optional[str]] = mapped_column(String(255))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255))
    utm_content: Mapped[Optional[str]] = mapped_column(String(255))
    utm_term: Mapped[Optional[str]] = mapped_column(String(255))
    yclid: Mapped[Optional[str]] = mapped_column(String(255))
    metrica_client_id: Mapped[Optional[str]] = mapped_column(String(255))
    referer: Mapped[Optional[str]] = mapped_column(String(2048))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    landing_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    redirect_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="LANDING_VISIT", nullable=False)
    campaign_metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    max_user_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    visited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    max_opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    to_channel_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

class MembershipEvent(Base):
    __tablename__ = "membership_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    visit_id: Mapped[Optional[str]] = mapped_column(ForeignKey("traffic_visits.id", ondelete="SET NULL"), index=True)
    landing_page_id: Mapped[str] = mapped_column(ForeignKey("landing_pages.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    user_max_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("visit_id", "channel_id", "user_max_id", "event_type"),)

class TrafficMembership(Base):
    __tablename__ = "traffic_memberships"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    current_member: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attributed_visit_id: Mapped[Optional[str]] = mapped_column(ForeignKey("traffic_visits.id", ondelete="SET NULL"), index=True)
    attribution_status: Mapped[str] = mapped_column(String(24), default="UNATTRIBUTED", nullable=False, index=True)
    attribution_score: Mapped[Optional[int]] = mapped_column(Integer)
    source_event_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("channel_id", "max_user_id"),)

class TrafficEvent(Base):
    __tablename__ = "traffic_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    visit_id: Mapped[Optional[str]] = mapped_column(ForeignKey("traffic_visits.id", ondelete="SET NULL"), index=True)
    max_user_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MaxIdentityLink(Base):
    __tablename__ = "max_identity_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    visitor_id: Mapped[str] = mapped_column(ForeignKey("traffic_visits.id", ondelete="CASCADE"), unique=True, index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    startapp_token: Mapped[str] = mapped_column(String(96), index=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(255))
    init_data_fingerprint: Mapped[str] = mapped_column(String(64))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("visitor_id", "max_user_id"),)


class TrafficSubscription(Base):
    __tablename__ = "traffic_subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    visitor_id: Mapped[str] = mapped_column(ForeignKey("traffic_visits.id", ondelete="CASCADE"), index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lost_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("visitor_id", "channel_id"),)

class TrafficAttributionCandidate(Base):
    __tablename__ = "traffic_attribution_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    membership_id: Mapped[str] = mapped_column(ForeignKey("traffic_memberships.id", ondelete="CASCADE"), index=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("traffic_visits.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("membership_id", "visit_id"),)

class OfflineConversion(Base):
    __tablename__ = "offline_conversion_queue"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    visit_id: Mapped[str] = mapped_column(ForeignKey("traffic_visits.id", ondelete="CASCADE"), index=True)
    membership_id: Mapped[str] = mapped_column(ForeignKey("traffic_memberships.id", ondelete="CASCADE"), index=True)
    target: Mapped[str] = mapped_column(String(32), default="YANDEX")
    identifier_type: Mapped[Optional[str]] = mapped_column(String(32))
    identifier_value: Mapped[Optional[str]] = mapped_column(String(255))
    conversion_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("visit_id", "membership_id", "target"),)
class BlockedUser(Base):
    __tablename__="blocked_users"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str]=mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    user_external_id: Mapped[str]=mapped_column(String(128), index=True)
    channel=relationship("Channel", back_populates="blocked_users")
    __table_args__=(UniqueConstraint("channel_id","user_external_id"),)
class Conversation(Base):
    __tablename__="conversations"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str]=mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    user_external_id: Mapped[str]=mapped_column(String(128), index=True)
    user_name: Mapped[str]=mapped_column(String(255), default="Пользователь")
    avatar_url: Mapped[Optional[str]]=mapped_column(String(2048))
    status: Mapped[str]=mapped_column(String(16), default="new")
    unread: Mapped[int]=mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    channel=relationship("Channel", back_populates="conversations")
    messages=relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")
class Message(Base):
    __tablename__="messages"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str]=mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str]=mapped_column(String(16))
    text: Mapped[str]=mapped_column(Text)
    image_url: Mapped[Optional[str]]=mapped_column(String(2048))
    attachments_json: Mapped[Optional[str]]=mapped_column(Text)
    admin_feed_delivered: Mapped[bool]=mapped_column(Boolean, default=False, nullable=False)
    is_self_test: Mapped[bool]=mapped_column(Boolean, default=False, nullable=False)
    reply_to_message_id: Mapped[Optional[str]]=mapped_column(String(128))
    external_message_id: Mapped[Optional[str]]=mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    conversation=relationship("Conversation", back_populates="messages")


class MaxJoinEvent(Base):
    __tablename__ = "max_join_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    added_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    raw_event_json: Mapped[Optional[str]] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MaxPendingJoinRequest(Base):
    __tablename__ = "max_pending_join_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    remote_request_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approve_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    user_added_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approve_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("channel_id", "max_user_id"),)

class AdminFeedMessage(Base):
    __tablename__="admin_feed_messages"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    admin_feed_message_id: Mapped[str]=mapped_column(String(128), unique=True, index=True)
    source_message_id: Mapped[str]=mapped_column(String(36), unique=True, index=True)
    source_external_message_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)
    source_user_id: Mapped[str]=mapped_column(String(128), index=True)
    channel_id: Mapped[Optional[str]]=mapped_column(String(36), index=True)
    feed_session_id: Mapped[str]=mapped_column(String(36), index=True)
    source_private_chat_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)
    source_bot_connection_id: Mapped[Optional[str]]=mapped_column(String(36), index=True)
    source_transport: Mapped[str]=mapped_column(String(20), default="SYSTEM_INBOX", nullable=False)
    destination_admin_chat_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)

class BotConnection(Base):
    __tablename__="bot_connections"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    owner_admin_id: Mapped[str]=mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    owner_max_user_id: Mapped[str]=mapped_column(String(128), index=True)
    max_bot_id: Mapped[str]=mapped_column(String(128), index=True)
    username: Mapped[str]=mapped_column(String(255))
    display_name: Mapped[str]=mapped_column(String(255))
    encrypted_token: Mapped[str]=mapped_column(Text)
    status: Mapped[str]=mapped_column(String(32), default="WAITING_ADMIN_START", index=True)
    admin_private_chat_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    welcome_text: Mapped[str]=mapped_column(Text, default="✅ Напишите ваш вопрос — я передам его администратору.")
    sent_text: Mapped[str]=mapped_column(Text, default="✅ Сообщение отправлено.\nОжидайте ответ.")

class CustomBotMessage(Base):
    __tablename__="custom_bot_messages"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    bot_connection_id: Mapped[str]=mapped_column(ForeignKey("bot_connections.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str]=mapped_column(String(16))
    source_user_id: Mapped[str]=mapped_column(String(128), index=True)
    source_private_chat_id: Mapped[str]=mapped_column(String(128), index=True)
    external_message_id: Mapped[Optional[str]]=mapped_column(String(128), unique=True, index=True)
    text: Mapped[Optional[str]]=mapped_column(Text)
    attachments_json: Mapped[Optional[str]]=mapped_column(Text)
    reply_to_message_id: Mapped[Optional[str]]=mapped_column(String(128))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)

class CustomBotSession(Base):
    __tablename__="custom_bot_sessions"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    bot_connection_id: Mapped[str]=mapped_column(ForeignKey("bot_connections.id", ondelete="CASCADE"), index=True)
    user_external_id: Mapped[str]=mapped_column(String(128), index=True)
    private_chat_id: Mapped[str]=mapped_column(String(128))
    # Kept nullable for backwards-compatible rows from the abandoned
    # channel-bound custom-bot experiment. Standalone bots never use it.
    current_channel_id: Mapped[Optional[str]]=mapped_column(String(36), index=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__=(UniqueConstraint("bot_connection_id","user_external_id"),)

class SupportTicket(Base):
    __tablename__="support_tickets"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    display_number: Mapped[int]=mapped_column(Integer, Sequence("support_ticket_display_number_seq", start=1001), unique=True, nullable=False)
    max_user_id: Mapped[str]=mapped_column(String(128), index=True)
    source_private_chat_id: Mapped[str]=mapped_column(String(128))
    status: Mapped[str]=mapped_column(String(20), default="NEW", nullable=False, index=True)
    support_chat_id: Mapped[str]=mapped_column(String(128))
    support_chat_message_id: Mapped[Optional[str]]=mapped_column(String(128), unique=True, index=True)
    source_user_name: Mapped[str]=mapped_column(String(255), default="Пользователь")
    source_text: Mapped[Optional[str]]=mapped_column(Text)
    attachments_json: Mapped[Optional[str]]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    taken_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))

class SupportTicketMessage(Base):
    __tablename__="support_ticket_messages"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    ticket_id: Mapped[str]=mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    source_message_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)
    support_chat_message_id: Mapped[str]=mapped_column(String(128), unique=True, index=True)
    source_user_id: Mapped[str]=mapped_column(String(128), index=True)
    direction: Mapped[str]=mapped_column(String(16), default="in")
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
