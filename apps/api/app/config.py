from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:54329/postgres"
    app_user_dsn: str = "postgresql://app_user:app_user@localhost:54329/postgres"
    app_webhook_dsn: str = "postgresql://app_webhook:app_webhook@localhost:54329/postgres"
    app_user_password: str = "app_user"
    app_webhook_password: str = "app_webhook"

    webhook_secret: str = "dev-webhook-secret"
    jwt_secret: str = "dev-jwt-secret-change-me"
    crm_url: str = "http://localhost:8090/crm"
    provider_url: str = "http://localhost:8090"

    # Browser origin for CORS + cookie-auth (Vite dev UI).
    web_origin: str = "http://localhost:5173"
    # HttpOnly cookie Secure flag — leave false on plain http localhost.
    cookie_secure: bool = False

    dev_token_enabled: bool = True
    migrations_dir: str = "migrations"

    crm_poll_interval_sec: float = 1.0
    reaper_interval_sec: float = 60.0
    stale_attempt_minutes: int = 30
    analysis_concurrency: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
