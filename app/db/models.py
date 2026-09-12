from __future__ import annotations

import uuid
from typing import Optional
from datetime import date, datetime, timezone
import sqlalchemy as sa
from sqlalchemy import String, Boolean, Date, DateTime, ForeignKey, Text, Integer, BigInteger, Numeric, UniqueConstraint, Sequence, CheckConstraint, Index
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
    onboarding_seen: Mapped[bool]=mapped_column(Boolean, default=False, nullable=False)
    self_test_channel_id: Mapped[Optional[str]]=mapped_column(String(36))
    pending_inbox_channel_id: Mapped[Optional[str]]=mapped_column(String(36))
    pending_traffic_channel_id: Mapped[Optional[str]]=mapped_column(String(36))
    support_input_mode: Mapped[bool]=mapped_column(Boolean, default=False, nullable=False)
    support_input_ticket_id: Mapped[Optional[str]]=mapped_column(String(36))
    subscription_expires_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True), index=True)
    subscription_tariff: Mapped[str] = mapped_column(String(16), default="free", server_default="free", nullable=False)
    ai_promotion_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    pending_free_trial: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    custom_bot_state: Mapped[Optional[str]]=mapped_column(String(32))
    pending_custom_bot_id: Mapped[Optional[str]]=mapped_column(String(36))
    # Durable context for the one-message AI objective input flow.  Keeping
    # this in the admin row lets a worker restart between the button tap and
    # the owner's numeric reply without losing the target object.
    pending_ads_object_type: Mapped[Optional[str]] = mapped_column(String(16))
    pending_ads_object_id: Mapped[Optional[str]] = mapped_column(String(128))
    pending_ads_campaign_id: Mapped[Optional[str]] = mapped_column(String(128))
    channels=relationship("Channel", back_populates="admin", cascade="all, delete-orphan")

class WebAuthnCredential(Base):
    __tablename__ = "web_admin_webauthn_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    admin_id: Mapped[str] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    credential_public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

class WebAdminAuthChallenge(Base):
    __tablename__ = "web_admin_auth_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    admin_id: Mapped[Optional[str]] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    challenge: Mapped[str] = mapped_column(String(512), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class WebAdminSession(Base):
    __tablename__ = "web_admin_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    admin_id: Mapped[str] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

class QaRun(Base):
    __tablename__ = "qa_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    release_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tested_sha: Mapped[str] = mapped_column(String(64), index=True)
    current_main_sha: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    production_sha: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    total_tests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    production_status: Mapped[str] = mapped_column(String(24), default="NOT_DEPLOYED", nullable=False)
    production_smoke: Mapped[str] = mapped_column(String(24), default="NOT_RUN", nullable=False)
    artifact_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    source_path: Mapped[Optional[str]] = mapped_column(String(255))
    failures_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    unverified_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    suites = relationship("QaSuiteResult", back_populates="run", cascade="all, delete-orphan")

class QaSuiteResult(Base):
    __tablename__ = "qa_suite_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    suite_name: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    total_tests: Mapped[Optional[int]] = mapped_column(Integer)
    passed: Mapped[Optional[int]] = mapped_column(Integer)
    failed: Mapped[Optional[int]] = mapped_column(Integer)
    skipped: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    failures_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    unverified_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    run = relationship("QaRun", back_populates="suites")
    __table_args__ = (UniqueConstraint("run_id", "suite_name", name="uq_qa_suite_result_run_name"),)

class ChannelSlugRegistry(Base):
    __tablename__="channel_slug_registry"
    id: Mapped[str]=mapped_column(String(36), primary_key=True, default=uid)
    max_channel_external_id: Mapped[str]=mapped_column(String(128), unique=True, index=True)
    public_slug: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    # Stable landing address for this MAX channel identity.
    landing_public_id: Mapped[Optional[str]]=mapped_column(String(64), unique=True, index=True)
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
    daily_reports_enabled: Mapped[bool]=mapped_column(Boolean, default=True, server_default="true", nullable=False)
    auto_approve_enabled: Mapped[bool]=mapped_column(Boolean, default=False, nullable=False)
    public_slug: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    inbox_chat_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)
    inbox_chat_title: Mapped[Optional[str]]=mapped_column(String(255))
    inbox_chat_connected_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    communication_mode: Mapped[str]=mapped_column(String(20), default="SYSTEM_INBOX", nullable=False)
    bot_connection_id: Mapped[Optional[str]]=mapped_column(String(36), index=True)
    max_connection_kind: Mapped[str]=mapped_column(String(24), default="PRIMARY", server_default="PRIMARY", nullable=False)
    max_owner_user_id: Mapped[Optional[str]]=mapped_column(String(128), index=True)
    max_chat_metadata_json: Mapped[Optional[str]]=mapped_column(Text)
    connected_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    welcome_text: Mapped[str]=mapped_column(Text, default="📩 Есть новость или интересный материал?\nПришлите сюда текст, фото или видео — редакция канала всё посмотрит.")
    sent_text: Mapped[str]=mapped_column(Text, default="✅ Спасибо! Материал получен и передан редакции на рассмотрение.")
    blocked_users=relationship("BlockedUser", back_populates="channel", cascade="all, delete-orphan")
    admin=relationship("Admin", back_populates="channels")
    conversations=relationship("Conversation", back_populates="channel", cascade="all, delete-orphan")

class SubscriptionOrder(Base):
    __tablename__ = "subscription_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    invoice_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    admin_id: Mapped[str] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True, nullable=True)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    # Service tier for new orders; NULL keeps historical Robokassa orders
    # compatible and is treated as Start by the payment callback.
    tariff: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    order_kind: Mapped[str] = mapped_column(String(24), default="service", server_default="service", nullable=False)
    amount: Mapped[Numeric] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FreeTrial(Base):
    """One service-level seven-day entitlement per verified MAX user."""
    __tablename__ = "free_trials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    admin_id: Mapped[str] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), unique=True, index=True)
    # This is a quota marker, not the scope of the service entitlement.
    activated_channel_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, index=True)
    trial_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trial_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)
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
    # OAuth credentials are encrypted before reaching the database.  Redis is
    # only a cache and must never be the sole record of an owner connection.
    yandex_oauth_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    yandex_oauth_connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    target_url: Mapped[Optional[str]] = mapped_column(String(2048))
    channel_mode: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    attribution_window_hours: Mapped[int] = mapped_column(Integer, default=72, nullable=False)
    conversion_policy: Mapped[str] = mapped_column(String(24), default="CONSERVATIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class SystemPromotionProfile(Base):
    """Independent promotion configuration for the MAXIBOT system identity."""
    __tablename__ = "system_promotion_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    system_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    public_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    landing_url: Mapped[Optional[str]] = mapped_column(String(2048))
    target_url: Mapped[Optional[str]] = mapped_column(String(2048))
    yandex_counter_id: Mapped[Optional[str]] = mapped_column(String(128))
    yandex_goal_name: Mapped[Optional[str]] = mapped_column(String(128))
    yandex_owner_login: Mapped[Optional[str]] = mapped_column(String(255))
    yandex_oauth_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    yandex_oauth_connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BotPromotionProfile(Base):
    """Canonical BOT-scoped promotion configuration.

    ``BotConnection`` owns credentials and runtime state.  This model owns
    promotion data for both SYSTEM (MaxiBot) and CUSTOMER bots.  The legacy
    ``SystemPromotionProfile`` remains available only for compatibility with
    historical landing records during the convergence rollout.
    """
    __tablename__ = "bot_promotion_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    bot_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    bot_type: Mapped[str] = mapped_column(String(16), default="CUSTOMER", nullable=False, index=True)
    owner_admin_id: Mapped[Optional[str]] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    bot_connection_id: Mapped[Optional[str]] = mapped_column(ForeignKey("bot_connections.id", ondelete="CASCADE"), unique=True, index=True)
    legacy_system_key: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    public_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    landing_url: Mapped[Optional[str]] = mapped_column(String(2048))
    target_url: Mapped[Optional[str]] = mapped_column(String(2048))
    yandex_counter_id: Mapped[Optional[str]] = mapped_column(String(128))
    yandex_goal_name: Mapped[Optional[str]] = mapped_column(String(128))
    yandex_owner_login: Mapped[Optional[str]] = mapped_column(String(255))
    yandex_oauth_token_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    yandex_oauth_connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class SystemTrafficVisit(Base):
    """A system-scoped ad visit; intentionally independent from channels."""
    __tablename__ = "system_traffic_visits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    yclid: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    utm_source: Mapped[Optional[str]] = mapped_column(String(255))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255))
    utm_content: Mapped[Optional[str]] = mapped_column(String(255))
    utm_term: Mapped[Optional[str]] = mapped_column(String(255))
    metrica_client_id: Mapped[Optional[str]] = mapped_column(String(255))
    referer: Mapped[Optional[str]] = mapped_column(String(2048))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    max_user_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="LANDING_VISIT", nullable=False)
    destination_type: Mapped[str] = mapped_column(String(16), default="BOT", server_default="BOT", nullable=False)
    experiment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    experiment_variant: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    provider_ad_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    provider_creative_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)


class SystemMaxIdentityLink(Base):
    """Verified MAX identity for one system-scoped BOT handoff."""
    __tablename__ = "system_max_identity_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    system_visit_id: Mapped[str] = mapped_column(
        ForeignKey("system_traffic_visits.id", ondelete="CASCADE"), unique=True, index=True
    )
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    startapp_token: Mapped[str] = mapped_column(String(96), index=True)
    init_data_fingerprint: Mapped[str] = mapped_column(String(64))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SystemTrafficEvent(Base):
    """Safe, aggregateable lifecycle event for a system promotion visit."""
    __tablename__ = "system_traffic_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    visit_id: Mapped[str] = mapped_column(ForeignKey("system_traffic_visits.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)

class SystemConversion(Base):
    __tablename__ = "system_conversion_queue"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    visit_id: Mapped[str] = mapped_column(ForeignKey("system_traffic_visits.id", ondelete="CASCADE"), index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    goal: Mapped[str] = mapped_column(String(64), default="bot_started", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("visit_id", "goal", name="uq_system_conversion_visit_goal"),)


class BotTrafficVisit(Base):
    """Generic promoted-BOT visit; CHANNEL traffic has a separate model."""
    __tablename__ = "bot_traffic_visits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    bot_id: Mapped[str] = mapped_column(String(128), index=True)
    destination_id: Mapped[str] = mapped_column(String(128), index=True)
    yclid: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    utm_source: Mapped[Optional[str]] = mapped_column(String(255))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(255))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(255))
    utm_content: Mapped[Optional[str]] = mapped_column(String(255))
    utm_term: Mapped[Optional[str]] = mapped_column(String(255))
    metrica_client_id: Mapped[Optional[str]] = mapped_column(String(255))
    referer: Mapped[Optional[str]] = mapped_column(String(2048))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    max_user_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="LANDING_VISIT", nullable=False)
    experiment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    experiment_variant: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    provider_ad_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    provider_creative_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)


class BotMaxIdentityLink(Base):
    __tablename__ = "bot_max_identity_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    bot_visit_id: Mapped[str] = mapped_column(ForeignKey("bot_traffic_visits.id", ondelete="CASCADE"), unique=True, index=True)
    bot_id: Mapped[str] = mapped_column(String(128), index=True)
    destination_id: Mapped[str] = mapped_column(String(128), index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    startapp_token: Mapped[str] = mapped_column(String(96), index=True)
    init_data_fingerprint: Mapped[str] = mapped_column(String(64))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BotTrafficEvent(Base):
    __tablename__ = "bot_traffic_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    bot_visit_id: Mapped[str] = mapped_column(ForeignKey("bot_traffic_visits.id", ondelete="CASCADE"), index=True)
    bot_id: Mapped[str] = mapped_column(String(128), index=True)
    destination_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)


class BotRecipientState(Base):
    """Current delivery state of a user in a bot dialog.

    This is deliberately separate from ``BlockedUser``: the latter is a
    per-channel moderation block, while this table records a MAX user removing
    (or restarting) the bot dialog so the owner dashboard can show a truthful
    current blocked count.
    """
    __tablename__ = "bot_recipient_states"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    bot_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", server_default="ACTIVE", index=True)
    blocked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    block_reason: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)
    __table_args__ = (
        UniqueConstraint("bot_key", "user_external_id", name="uq_bot_recipient_state_bot_user"),
    )


class BotConversion(Base):
    __tablename__ = "bot_conversion_queue"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    bot_visit_id: Mapped[str] = mapped_column(ForeignKey("bot_traffic_visits.id", ondelete="CASCADE"), index=True)
    bot_id: Mapped[str] = mapped_column(String(128), index=True)
    destination_id: Mapped[str] = mapped_column(String(128), index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    paid_journey_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    internal_event: Mapped[str] = mapped_column(String(64), default="bot_started", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_result: Mapped[Optional[str]] = mapped_column(String(32))
    last_error_class: Mapped[Optional[str]] = mapped_column(String(64))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("bot_visit_id", "internal_event", name="uq_bot_conversion_visit_event"),
        UniqueConstraint("paid_journey_key", name="uq_bot_conversion_paid_journey"),
    )

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
    destination_type: Mapped[str] = mapped_column(String(16), default="CHANNEL", server_default="CHANNEL", nullable=False)
    experiment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    experiment_variant: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    provider_ad_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    provider_creative_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

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
    # A bounded diagnostic category; it never contains an identifier, token,
    # URL, or user supplied text.  It explains why this member is not an
    # eligible paid conversion.
    attribution_reason: Mapped[str] = mapped_column(String(64), default="UNATTRIBUTED", nullable=False, index=True)
    attribution_score: Mapped[Optional[int]] = mapped_column(Integer)
    source_event_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("channel_id", "max_user_id"),)


class DailyChannelReport(Base):
    """One idempotent owner delivery for a channel and a Moscow calendar day."""
    __tablename__ = "daily_channel_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    subscriber_count: Mapped[Optional[int]] = mapped_column(Integer)
    subscriber_delta: Mapped[Optional[int]] = mapped_column(Integer)
    subscribed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unsubscribed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invite_link_unsubscribed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    undetermined_unsubscribed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("channel_id", "report_date", name="uq_daily_channel_report_date"),)

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


class TrafficJoinIntent(Base):
    """The most recent verified request to open one channel.

    MAX does not include a click/visit token in its later ``user_added``
    update.  This record binds that official event to the last *verified*
    Mini App channel handoff for the same MAX user and channel.  It never
    stores the raw startapp token, yclid, initData, or a channel URL.
    """
    __tablename__ = "traffic_join_intents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    visit_id: Mapped[str] = mapped_column(ForeignKey("traffic_visits.id", ondelete="CASCADE"), unique=True, index=True)
    max_user_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Membership reconciliation rotates through active intents after a MAX
    # worker restart.  Persisting this prevents older unjoined intents from
    # starving newer approved members in a bounded batch.
    membership_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("visit_id", "channel_id", name="uq_traffic_join_intent_visit_channel"),)


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
    goal: Mapped[str] = mapped_column(String(64), default="userDidSubscribe", server_default="userDidSubscribe", nullable=False)
    identifier_type: Mapped[Optional[str]] = mapped_column(String(32))
    identifier_value: Mapped[Optional[str]] = mapped_column(String(255))
    conversion_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # A successful upload is asynchronous in Metrica.  Keep its identifier
    # and terminal state instead of treating HTTP 200 as a delivered goal.
    metrica_upload_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    metrica_upload_status: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    metrica_upload_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("visit_id", "membership_id", "target", "goal", name="uq_offline_conversion_visit_membership_target_goal"),)
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


class MaxPollingCheckpoint(Base):
    """Durable cursor for the sole production MAX long-poll consumer.

    Losing an in-memory marker on a worker restart can make MAX advance the
    stream before this process has seen membership updates.  The checkpoint is
    committed only after every update in a response was handled successfully.
    """
    __tablename__ = "max_polling_checkpoints"
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    marker: Mapped[Optional[int]] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


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
    # Stable bot-scoped landing address; independent from channel and system
    # promotion identities.
    public_id: Mapped[Optional[str]]=mapped_column(String(64), unique=True, index=True)
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


# AI promotion V1.  These records intentionally keep a durable, inspectable
# history and contain no OAuth tokens, raw user data, or Direct write intents.
class AdsAccount(Base):
    __tablename__ = "ads_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    admin_id: Mapped[str] = mapped_column(ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="YANDEX_DIRECT", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="CONNECTED", nullable=False, index=True)
    external_account_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    connection_status: Mapped[Optional[str]] = mapped_column(String(24), index=True)
    provider_metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    metadata_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("admin_id", "provider", name="uq_ads_account_provider"),)


class AdsCampaign(Base):
    __tablename__ = "ads_campaigns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("ads_accounts.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(512))
    state: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[Optional[str]] = mapped_column(String(64))
    raw_json: Mapped[Optional[str]] = mapped_column(Text)
    ads_account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_accounts.id", ondelete="CASCADE"), index=True)
    external_campaign_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    campaign_type: Mapped[Optional[str]] = mapped_column(String(64))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    strategy_type: Mapped[Optional[str]] = mapped_column(String(128))
    budget_type: Mapped[Optional[str]] = mapped_column(String(64))
    daily_budget: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    weekly_budget: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    currency: Mapped[Optional[str]] = mapped_column(String(16))
    optimization_goal: Mapped[Optional[str]] = mapped_column(String(128))
    target_cpa: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    target_roi: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    target_conversion_value: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    settings_json: Mapped[Optional[str]] = mapped_column(Text)
    provider_metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    config_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    metadata_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("account_id", "external_id", name="uq_ads_campaign_external"),)


class AdsPromotionCampaignLink(Base):
    """Provider-neutral campaign link for a CHANNEL or BOT promotion object."""
    __tablename__ = "ads_promotion_campaign_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    object_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    channel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    bot_promotion_profile_id: Mapped[Optional[str]] = mapped_column(ForeignKey("bot_promotion_profiles.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False, index=True)
    # MaxiBot owner objective.  This is deliberately scoped to the object ↔
    # campaign link; AdsCampaign.target_cpa remains provider metadata.
    objective_type: Mapped[Optional[str]] = mapped_column(String(32))
    target_cpa: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    objective_status: Mapped[Optional[str]] = mapped_column(String(16))
    objective_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Object-level execution policy.  Existing rows default to advisory so a
    # deployment can never turn on provider writes implicitly.
    execution_mode: Mapped[str] = mapped_column(String(16), default="ADVISORY", server_default="ADVISORY", nullable=False, index=True)
    autopilot_config_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}", nullable=False)
    execution_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (
        CheckConstraint(
            "(object_type = 'CHANNEL' AND channel_id IS NOT NULL AND bot_promotion_profile_id IS NULL) OR "
            "(object_type = 'BOT' AND channel_id IS NULL AND bot_promotion_profile_id IS NOT NULL)",
            name="ck_ads_promotion_campaign_link_one_object",
        ),
        Index("uq_ads_promotion_campaign_channel", "channel_id", "campaign_id", unique=True, postgresql_where=sa.text("object_type = 'CHANNEL'")),
        Index("uq_ads_promotion_campaign_bot", "bot_promotion_profile_id", "campaign_id", unique=True, postgresql_where=sa.text("object_type = 'BOT'")),
    )


class AdsAnalysisSnapshot(Base):
    __tablename__ = "ads_analysis_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    promotion_object_type: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    promotion_object_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_campaigns.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    period_from: Mapped[Optional[date]] = mapped_column(Date)
    period_to: Mapped[Optional[date]] = mapped_column(Date)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AdsCreative(Base):
    __tablename__ = "ads_creatives"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    ad_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_entities.id", ondelete="SET NULL"), index=True)
    external_creative_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    creative_type: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[Optional[str]] = mapped_column(Text)
    title2: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)
    cta: Mapped[Optional[str]] = mapped_column(String(512))
    href: Mapped[Optional[str]] = mapped_column(String(2048))
    display_url: Mapped[Optional[str]] = mapped_column(String(2048))
    image_ref: Mapped[Optional[str]] = mapped_column(String(2048))
    video_ref: Mapped[Optional[str]] = mapped_column(String(2048))
    source_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    storage_provider: Mapped[Optional[str]] = mapped_column(String(32))
    storage_key: Mapped[Optional[str]] = mapped_column(String(1024), index=True)
    media_storage_state: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128))
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    ai_features_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    provider_state: Mapped[Optional[str]] = mapped_column(String(64))
    provider_metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("campaign_id", "external_creative_id", name="uq_ads_creative_external"),)


class AdsEntity(Base):
    """Provider-independent canonical entity registry."""
    __tablename__ = "ads_entities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_campaigns.id", ondelete="CASCADE"), index=True)
    external_entity_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_entities.id", ondelete="SET NULL"), index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(512))
    status: Mapped[Optional[str]] = mapped_column(String(64))
    provider_metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    metadata_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("provider", "entity_type", "provider_entity_id", name="uq_ads_entity_identity"),)


class AdsReportRun(Base):
    __tablename__ = "ads_report_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(128), nullable=False)
    campaign_provider_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    date_from: Mapped[Optional[date]] = mapped_column(Date)
    date_to: Mapped[Optional[date]] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)
    requested_fields_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    request_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="SUCCESS", nullable=False, index=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(96))
    freshness_caveat: Mapped[Optional[str]] = mapped_column(Text)


class AdsCollectionRun(Base):
    """Idempotent server-side read-only collection slot/run."""
    __tablename__ = "ads_collection_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_campaigns.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    collection_type: Mapped[str] = mapped_column(String(32), default="MANUAL_AI_SYNC", nullable=False)
    run_type: Mapped[str] = mapped_column(String(48), default="PERIODIC_CAMPAIGN", nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="PERIODIC", nullable=False)
    summary_json: Mapped[Optional[str]] = mapped_column(Text)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    slot_key: Mapped[str] = mapped_column(String(192), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_analysis_snapshots.id", ondelete="SET NULL"), index=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(96))
    freshness_caveat: Mapped[Optional[str]] = mapped_column(Text)


class AdsObservation(Base):
    __tablename__ = "ads_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_analysis_snapshots.id", ondelete="SET NULL"), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_entities.id", ondelete="SET NULL"), index=True)
    dimension_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    dimension_value: Mapped[Optional[str]] = mapped_column(String(256))
    window: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    date_from: Mapped[Optional[date]] = mapped_column(Date)
    date_to: Mapped[Optional[date]] = mapped_column(Date)
    impressions: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    clicks: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    cost: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    conversions: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    fact_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    report_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_report_runs.id", ondelete="SET NULL"), index=True)
    collection_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_collection_runs.id", ondelete="SET NULL"), index=True)
    logical_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    provenance_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AdsDailyAggregate(Base):
    """Canonical downsample target; raw hourly rows are retained in dry-run mode."""
    __tablename__ = "ads_daily_aggregates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("ads_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_entities.id", ondelete="SET NULL"), index=True)
    dimension_type: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension_value: Mapped[Optional[str]] = mapped_column(String(256))
    aggregate_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    impressions: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    clicks: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    cost: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    conversions: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    source_observation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    source_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    aggregation_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    provenance_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    __table_args__ = (UniqueConstraint("provider", "campaign_id", "entity_id", "dimension_type", "dimension_value", "aggregate_date", name="uq_ads_daily_aggregate_identity"),)


class AdsAiOutcome(Base):
    __tablename__ = "ads_ai_outcomes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("ads_recommendations.id", ondelete="CASCADE"), index=True)
    experiment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False, index=True)
    execution_status: Mapped[str] = mapped_column(String(32), default="RECOMMENDATION_ONLY", nullable=False)
    baseline_snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_analysis_snapshots.id", ondelete="SET NULL"), index=True)
    followup_snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_analysis_snapshots.id", ondelete="SET NULL"), index=True)
    observed_delta_json: Mapped[Optional[str]] = mapped_column(Text)
    error_code: Mapped[Optional[str]] = mapped_column(String(96))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    evaluation_status: Mapped[Optional[str]] = mapped_column(String(24), index=True)
    implementation_confirmed: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    implementation_evidence_json: Mapped[Optional[str]] = mapped_column(Text)
    baseline_metrics_json: Mapped[Optional[str]] = mapped_column(Text)
    expected_direction: Mapped[Optional[str]] = mapped_column(String(16))
    expected_impact_json: Mapped[Optional[str]] = mapped_column(Text)
    actual_metrics_json: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[str]] = mapped_column(String(16))
    reason_json: Mapped[Optional[str]] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("recommendation_id", "horizon", name="uq_ads_ai_outcome_horizon"),)


class AdsAiRun(Base):
    __tablename__ = "ads_ai_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    promotion_object_type: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    promotion_object_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default="NOT_RUN", nullable=False, index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(64))
    model: Mapped[Optional[str]] = mapped_column(String(128))
    input_snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_analysis_snapshots.id", ondelete="SET NULL"))
    output_json: Mapped[Optional[str]] = mapped_column(Text)
    campaign_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_campaigns.id", ondelete="SET NULL"), index=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64))
    schema_version: Mapped[Optional[str]] = mapped_column(String(64))
    safe_context_json: Mapped[Optional[str]] = mapped_column(Text)
    raw_structured_output_json: Mapped[Optional[str]] = mapped_column(Text)
    validated_output_json: Mapped[Optional[str]] = mapped_column(Text)
    validation_error: Mapped[Optional[str]] = mapped_column(Text)
    worker_diagnostics_json: Mapped[Optional[str]] = mapped_column(Text)
    worker_release_id: Mapped[Optional[str]] = mapped_column(String(128))
    policy_result_json: Mapped[Optional[str]] = mapped_column(Text)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    lease_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Origin of the user-triggered dashboard action.  This lets the existing
    # MAX worker update the exact panel after the result is committed.
    ui_admin_external_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    ui_message_id: Mapped[Optional[str]] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AdsRecommendation(Base):
    __tablename__ = "ads_recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    experiment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    experiment_seq: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    experiment_status: Mapped[str] = mapped_column(String(24), default="planned", server_default="planned", nullable=False, index=True)
    channel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    ai_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_ai_runs.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    title: Mapped[str] = mapped_column(String(512))
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    action_code: Mapped[str] = mapped_column(String(64), default="INSUFFICIENT_DATA", nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)
    data_sufficiency: Mapped[str] = mapped_column(String(24), default="INSUFFICIENT", nullable=False)
    policy_json: Mapped[Optional[str]] = mapped_column(Text)
    problem_json: Mapped[Optional[str]] = mapped_column(Text)
    root_cause_json: Mapped[Optional[str]] = mapped_column(Text)
    hypothesis: Mapped[Optional[str]] = mapped_column(Text)
    expected_impact_json: Mapped[Optional[str]] = mapped_column(Text)
    evaluation_window_days: Mapped[Optional[int]] = mapped_column(Integer)
    evaluation_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    promotion_object_type: Mapped[Optional[str]] = mapped_column(String(16))
    promotion_object_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    experiment_kind: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    control_ad_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_entities.id", ondelete="SET NULL"), index=True)
    control_creative_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_creatives.id", ondelete="SET NULL"), index=True)
    treatment_ad_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_entities.id", ondelete="SET NULL"), index=True)
    treatment_creative_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_creatives.id", ondelete="SET NULL"), index=True)
    creative_selection_reason: Mapped[Optional[str]] = mapped_column(Text)
    provider_lifecycle: Mapped[str] = mapped_column(String(32), default="PLANNED", server_default="PLANNED", nullable=False, index=True)
    measurement_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    control_variant_token: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    treatment_variant_token: Mapped[Optional[str]] = mapped_column(String(64), index=True)


class AdsMemory(Base):
    __tablename__ = "ads_memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    experiment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    channel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    promotion_object_type: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    promotion_object_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(24), default="PRIVATE", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="CANDIDATE", nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), default="OBSERVATION", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    campaign_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_campaigns.id", ondelete="SET NULL"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="AI_INFERENCE", nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)
    superseded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_memories.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AdsMemoryEvidence(Base):
    __tablename__ = "ads_memory_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    experiment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("ads_memories.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_ref_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_analysis_snapshots.id", ondelete="SET NULL"))
    ai_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads_ai_runs.id", ondelete="SET NULL"))
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AdsDataHealth(Base):
    __tablename__ = "ads_data_health"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    promotion_object_type: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    promotion_object_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


# Import compatibility for bounded historical tests/callers.  Runtime tables
# and all new persistence use the canonical physical names above.
AdsSnapshot = AdsAnalysisSnapshot
AdsV2Entity = AdsEntity
AdsV2ReportRun = AdsReportRun
AdsV2CollectionRun = AdsCollectionRun
AdsV2Observation = AdsObservation
AdsV2DailyAggregate = AdsDailyAggregate
AdsV2Outcome = AdsAiOutcome
AdsSyncRun = AdsCollectionRun
