from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://maxibot:maxibot@db:5432/maxibot"
    redis_url: str = "redis://redis:6379/0"
    app_secret: str = "dev-secret"
    max_client_mode: str = "stub"
    max_bot_token: str | None = None
    max_bot_username: str = "maxibot"
    max_miniapp_url: str | None = None
    max_public_url: str = "https://go.maxibot.pro"
    traffic_public_base_url: str = "https://go.maxibot.pro"
    max_landing_base_url: str = "https://go.maxibot.pro"
    traffic_attribution_window_hours: int = 72
    traffic_conversion_policy: str = "CONSERVATIVE"
    traffic_bot_attribution_enabled: bool = False
    traffic_startapp_ttl_seconds: int = 900
    traffic_attribution_enabled: bool = True
    attribution_join_window_seconds: int = 120
    order_matching_enabled: bool = False
    yandex_min_confidence: str = "HIGH"
    yandex_conversion_dry_run: bool = True
    yandex_oauth_client_id: str | None = None
    yandex_oauth_client_secret: str | None = None
    yandex_oauth_redirect_uri: str | None = None
    yandex_oauth_scope: str = "metrika:write metrika:offline_data"
    yandex_oauth_authorize_url: str = "https://oauth.yandex.ru/authorize"
    yandex_oauth_token_url: str = "https://oauth.yandex.ru/token"
    yandex_metrica_api_url: str = "https://api-metrika.yandex.net"
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
    max_api_base_url: str = "https://platform-api2.max.ru"
    max_updates_mode: str = "longpoll"
    max_webhook_url: str | None = None
    max_webhook_secret: str | None = None
    max_support_chat_id: str | None = Field(default=None, validation_alias="MAXIBOT_SUPPORT_CHAT_ID")
    maxibot_owner_user_id: str | None = Field(default=None, validation_alias="MAXIBOT_OWNER_USER_ID")
    custom_bot_token_encryption_key: str | None = Field(default=None, validation_alias="CUSTOM_BOT_TOKEN_ENCRYPTION_KEY")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_real_max(self) -> bool:
        return self.max_client_mode.lower() == "real"

    @property
    def max_auto_approve_allowed_chat_ids(self) -> set[str]:
        return {value.strip() for value in self.max_auto_approve_chat_ids.split(",") if value.strip()}


settings = Settings()

if settings.is_real_max and not settings.max_bot_token:
    raise RuntimeError("MAX_BOT_TOKEN is required when MAX_CLIENT_MODE=real")

if settings.max_client_mode.lower() not in {"stub", "real"}:
    raise RuntimeError("MAX_CLIENT_MODE must be stub or real")
