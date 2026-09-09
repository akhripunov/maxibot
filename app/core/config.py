from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    maxibot_release_id: str = Field(default="dev-local", validation_alias="MAXIBOT_RELEASE_ID")
    maxibot_commit_sha: str = Field(default="UNKNOWN", validation_alias="MAXIBOT_COMMIT_SHA")
    database_url: str = "postgresql+asyncpg://maxibot:maxibot@db:5432/maxibot"
    redis_url: str = "redis://redis:6379/0"
    app_secret: str = "dev-secret"
    max_client_mode: str = "stub"
    max_bot_token: str | None = None
    max_bot_username: str = "maxibot"
    max_bot_id: str = Field(default="maxibot", validation_alias="MAX_BOT_ID")
    max_miniapp_url: str | None = None
    max_public_url: str = "https://go.maxibot.pro"
    traffic_public_base_url: str = "https://go.maxibot.pro"
    max_landing_base_url: str = "https://go.maxibot.pro"
    uploads_dir: str = Field(default="/app/uploads", validation_alias="UPLOADS_DIR")
    robokassa_merchant_login: str | None = Field(default=None, validation_alias="ROBOKASSA_MERCHANT_LOGIN")
    robokassa_password1: str | None = Field(default=None, validation_alias="ROBOKASSA_PASSWORD1")
    robokassa_password2: str | None = Field(default=None, validation_alias="ROBOKASSA_PASSWORD2")
    robokassa_test_password1: str | None = Field(default=None, validation_alias="ROBOKASSA_TEST_PASSWORD1")
    robokassa_test_password2: str | None = Field(default=None, validation_alias="ROBOKASSA_TEST_PASSWORD2")
    robokassa_test_mode: bool = Field(default=True, validation_alias="ROBOKASSA_TEST_MODE")
    robokassa_payment_url: str = "https://auth.robokassa.ru/Merchant/Index.aspx"
    traffic_attribution_window_hours: int = 72
    traffic_conversion_policy: str = "CONSERVATIVE"
    traffic_bot_attribution_enabled: bool = False
    system_conversion_goal_map: str = Field(
        default="bot_started=bot_started,maxibot_channel_connected=maxibot_channel_connected",
        validation_alias="SYSTEM_CONVERSION_GOAL_MAP",
    )
    traffic_startapp_ttl_seconds: int = 900
    traffic_attribution_enabled: bool = True
    attribution_join_window_seconds: int = 120
    order_matching_enabled: bool = False
    yandex_min_confidence: str = "HIGH"
    yandex_conversion_dry_run: bool = True
    yandex_oauth_client_id: str | None = None
    yandex_oauth_client_secret: str | None = None
    yandex_oauth_redirect_uri: str | None = None
    yandex_oauth_scope: str = "metrika:read metrika:write metrika:offline_data direct:api"
    yandex_oauth_authorize_url: str = "https://oauth.yandex.ru/authorize"
    yandex_oauth_token_url: str = "https://oauth.yandex.ru/token"
    yandex_metrica_api_url: str = "https://api-metrika.yandex.net"
    yandex_direct_api_url: str = "https://api.direct.yandex.com/json"
    yandex_direct_api_version: str = "v5"
    max_join_request_signal: bool = False
    max_open_signal_enabled: bool = True
    max_open_likely_enabled: bool = True
    max_open_confirmed_enabled: bool = False
    traffic_browser_open_telemetry_enabled: bool = False
    max_open_likely_score: float = 0.8
    max_open_signal_window_seconds: int = 30
    max_auto_approve_enabled: bool = False
    max_pending_poll_enabled: bool = False
    max_pending_poll_interval_seconds: int = 3
    max_auto_approve_poll_interval_sec: int = 5
    max_auto_approve_chat_ids: str = ""
    max_auto_approve_delay_ms: int = 0
    max_auto_approve_max_attempts: int = 3
    bot_start_wait_seconds: int = Field(default=30, ge=0, validation_alias="BOT_START_WAIT_SECONDS")
    max_api_base_url: str = "https://platform-api2.max.ru"
    max_updates_mode: str = "longpoll"
    max_webhook_url: str | None = None
    max_webhook_secret: str | None = None
    max_support_chat_id: str | None = Field(default=None, validation_alias="MAXIBOT_SUPPORT_CHAT_ID")
    maxibot_owner_user_id: str | None = Field(default=None, validation_alias="MAXIBOT_OWNER_USER_ID")
    maxibot_support_url: str | None = Field(default=None, validation_alias="MAXIBOT_SUPPORT_URL")
    web_admin_internal_token: str | None = Field(default=None, validation_alias="WEB_ADMIN_INTERNAL_TOKEN")
    qa_result_ingest_token: str | None = Field(default=None, validation_alias="QA_RESULT_INGEST_TOKEN")
    web_admin_rp_id: str = Field(default="admin.maxibot.pro", validation_alias="WEB_ADMIN_RP_ID")
    web_admin_origin: str = Field(default="https://admin.maxibot.pro", validation_alias="WEB_ADMIN_ORIGIN")
    web_admin_rp_name: str = Field(default="MAXIBOT Owner Console", validation_alias="WEB_ADMIN_RP_NAME")
    web_admin_session_ttl_seconds: int = Field(default=28800, validation_alias="WEB_ADMIN_SESSION_TTL_SECONDS")
    web_admin_cookie_secure: bool = Field(default=True, validation_alias="WEB_ADMIN_COOKIE_SECURE")
    web_admin_passkey_bootstrap_token_hash: str | None = Field(default=None, validation_alias="WEB_ADMIN_PASSKEY_BOOTSTRAP_TOKEN_HASH")
    # AI promotion is deliberately opt-in and read-only in V1.  These flags
    # are checked on the server for every callback; a hidden button alone is
    # never treated as an access-control boundary.
    ads_enabled: bool = Field(default=False, validation_alias="ADS_ENABLED")
    ads_admin_only: bool = Field(default=True, validation_alias="ADS_ADMIN_ONLY")
    ads_llm_enabled: bool = Field(default=False, validation_alias="ADS_LLM_ENABLED")
    ads_llm_provider: str | None = Field(default=None, validation_alias="ADS_LLM_PROVIDER")
    ads_llm_model: str | None = Field(default=None, validation_alias="ADS_LLM_MODEL")
    ads_llm_api_key: str | None = Field(default=None, validation_alias="ADS_LLM_API_KEY")
    ads_llm_base_url: str | None = Field(default=None, validation_alias="ADS_LLM_BASE_URL")
    ads_write_actions_enabled: bool = Field(default=False, validation_alias="ADS_WRITE_ACTIONS_ENABLED")
    ads_autopilot_enabled: bool = Field(default=False, validation_alias="ADS_AUTOPILOT_ENABLED")
    ads_global_learning_enabled: bool = Field(default=False, validation_alias="ADS_GLOBAL_LEARNING_ENABLED")
    ads_background_analysis_enabled: bool = Field(default=False, validation_alias="ADS_BACKGROUND_ANALYSIS_ENABLED")
    # Periodic ADS collection is a separate read-only subsystem.  It is
    # fail-closed and never creates an AdsAiRun or invokes Codex.
    ads_periodic_collection_enabled: bool = Field(default=False, validation_alias="ADS_PERIODIC_COLLECTION_ENABLED")
    ads_collection_campaign_interval_minutes: int = Field(default=60, validation_alias="ADS_COLLECTION_CAMPAIGN_INTERVAL_MINUTES")
    ads_collection_granular_interval_minutes: int = Field(default=360, validation_alias="ADS_COLLECTION_GRANULAR_INTERVAL_MINUTES")
    ads_collection_search_query_enabled: bool = Field(default=False, validation_alias="ADS_COLLECTION_SEARCH_QUERY_ENABLED")
    ads_collection_search_query_interval_minutes: int = Field(default=1440, validation_alias="ADS_COLLECTION_SEARCH_QUERY_INTERVAL_MINUTES")
    ads_collection_search_query_retention_days: int = Field(default=30, validation_alias="ADS_COLLECTION_SEARCH_QUERY_RETENTION_DAYS")
    ads_collection_entity_sync_interval_minutes: int = Field(default=1440, validation_alias="ADS_COLLECTION_ENTITY_SYNC_INTERVAL_MINUTES")
    ads_collection_max_slots_per_cycle: int = Field(default=2, validation_alias="ADS_COLLECTION_MAX_SLOTS_PER_CYCLE")
    ads_collection_outcome_tolerance_minutes: int = Field(default=120, validation_alias="ADS_COLLECTION_OUTCOME_TOLERANCE_MINUTES")
    ads_analysis_cadence_minutes: int = Field(default=360, ge=1, validation_alias="ADS_ANALYSIS_CADENCE_MINUTES")
    ads_periodic_collection_canary_campaigns: str = Field(default="", validation_alias="ADS_PERIODIC_COLLECTION_CANARY_CAMPAIGNS")
    ads_observations_retention_days: int = Field(default=60, validation_alias="ADS_OBSERVATIONS_RETENTION_DAYS")
    ads_operational_retention_days: int = Field(default=90, validation_alias="ADS_OPERATIONAL_RETENTION_DAYS")
    ads_data_health_retention_days: int = Field(default=90, validation_alias="ADS_DATA_HEALTH_RETENTION_DAYS")
    ads_retention_enabled: bool = Field(default=True, validation_alias="ADS_RETENTION_ENABLED")
    ads_retention_batch_size: int = Field(default=500, validation_alias="ADS_RETENTION_BATCH_SIZE")
    ads_retention_interval_minutes: int = Field(default=1440, validation_alias="ADS_RETENTION_INTERVAL_MINUTES")
    ads_collection_raw_debug_enabled: bool = Field(default=False, validation_alias="ADS_COLLECTION_RAW_DEBUG_ENABLED")
    ads_storage_warning_free_percent: float = Field(default=15.0, validation_alias="ADS_STORAGE_WARNING_FREE_PERCENT")
    ads_storage_critical_free_percent: float = Field(default=8.0, validation_alias="ADS_STORAGE_CRITICAL_FREE_PERCENT")
    mail_cloud_enabled: bool = Field(default=False, validation_alias="MAIL_CLOUD_ENABLED")
    mail_cloud_webdav_url: str | None = Field(default=None, validation_alias="MAIL_CLOUD_WEBDAV_URL")
    mail_cloud_login: str | None = Field(default=None, validation_alias="MAIL_CLOUD_LOGIN")
    mail_cloud_app_password: str | None = Field(default=None, validation_alias="MAIL_CLOUD_APP_PASSWORD")
    mail_cloud_creative_root: str = Field(default="/MaxiBot ADS Creatives", validation_alias="MAIL_CLOUD_CREATIVE_ROOT")
    mail_cloud_max_media_bytes: int = Field(default=50 * 1024 * 1024, ge=1, validation_alias="MAIL_CLOUD_MAX_MEDIA_BYTES")
    mail_cloud_timeout_seconds: float = Field(default=20.0, gt=0, validation_alias="MAIL_CLOUD_TIMEOUT_SECONDS")
    mail_cloud_retry_attempts: int = Field(default=2, ge=0, le=5, validation_alias="MAIL_CLOUD_RETRY_ATTEMPTS")
    mail_cloud_retry_backoff_seconds: float = Field(default=0.5, ge=0, validation_alias="MAIL_CLOUD_RETRY_BACKOFF_SECONDS")
    ads_codex_worker_enabled: bool = Field(default=False, validation_alias="ADS_CODEX_WORKER_ENABLED")
    ads_codex_worker_token: str | None = Field(default=None, validation_alias="ADS_CODEX_WORKER_TOKEN")
    ads_codex_worker_poll_seconds: int = Field(default=15, validation_alias="ADS_CODEX_WORKER_POLL_SECONDS")
    ads_codex_worker_lease_seconds: int = Field(default=300, validation_alias="ADS_CODEX_WORKER_LEASE_SECONDS")
    ads_codex_worker_max_retries: int = Field(default=3, validation_alias="ADS_CODEX_WORKER_MAX_RETRIES")
    ads_codex_execution_timeout_seconds: int = Field(default=120, validation_alias="ADS_CODEX_EXECUTION_TIMEOUT_SECONDS")
    ads_ai_context_max_bytes: int = Field(default=75000, validation_alias="ADS_AI_CONTEXT_MAX_BYTES")
    ads_ai_evidence_max_entries: int = Field(default=120, validation_alias="ADS_AI_EVIDENCE_MAX_ENTRIES")
    ads_ai_max_adgroup_facts: int = Field(default=12, validation_alias="ADS_AI_MAX_ADGROUP_FACTS")
    ads_ai_max_ad_facts: int = Field(default=12, validation_alias="ADS_AI_MAX_AD_FACTS")
    ads_ai_max_placement_facts: int = Field(default=30, validation_alias="ADS_AI_MAX_PLACEMENT_FACTS")
    ads_ai_max_region_facts: int = Field(default=20, validation_alias="ADS_AI_MAX_REGION_FACTS")
    ads_ai_max_device_facts: int = Field(default=12, validation_alias="ADS_AI_MAX_DEVICE_FACTS")
    ads_ai_max_demographic_facts: int = Field(default=16, validation_alias="ADS_AI_MAX_DEMOGRAPHIC_FACTS")
    ads_ai_max_criteria_facts: int = Field(default=12, validation_alias="ADS_AI_MAX_CRITERIA_FACTS")
    ads_sync_interval_seconds: int = Field(default=900, validation_alias="ADS_SYNC_INTERVAL_SECONDS")

    @property
    def ads_hourly_retention_days(self) -> int:
        """Backward-compatible name for the canonical observations policy."""
        return self.ads_observations_retention_days

    @property
    def ads_daily_retention_days(self) -> int:
        """Daily aggregates are long-term; zero means no expiry."""
        return 0
    custom_bot_token_encryption_key: str | None = Field(default=None, validation_alias="CUSTOM_BOT_TOKEN_ENCRYPTION_KEY")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_real_max(self) -> bool:
        return self.max_client_mode.lower() == "real"

    @property
    def max_auto_approve_allowed_chat_ids(self) -> set[str]:
        return {value.strip() for value in self.max_auto_approve_chat_ids.split(",") if value.strip()}

    @property
    def ads_periodic_canary_campaign_ids(self) -> set[str]:
        return {value.strip() for value in self.ads_periodic_collection_canary_campaigns.split(",") if value.strip()}


settings = Settings()

if settings.is_real_max and not settings.max_bot_token:
    raise RuntimeError("MAX_BOT_TOKEN is required when MAX_CLIENT_MODE=real")

if settings.max_client_mode.lower() not in {"stub", "real"}:
    raise RuntimeError("MAX_CLIENT_MODE must be stub or real")
