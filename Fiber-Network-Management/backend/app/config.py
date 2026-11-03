"""Application configuration.

All settings come from environment variables prefixed with ``FNMS_`` (or a
``.env`` file in the backend directory). Nothing here depends on EazPay.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FNMS_", env_file=".env", extra="ignore")

    app_name: str = "Fiber Network Management"
    environment: str = Field("development", description="development | test | production")

    # Database. PostgreSQL recommended for production; SQLite is fine for development/demo.
    database_url: str = "sqlite:///./fnms.db"

    # Secrets. MUST be overridden in production.
    jwt_secret: str = "dev-only-change-me-jwt-secret-0123456789abcdef"
    # Fernet key (urlsafe base64, 32 bytes). Used to encrypt device credentials at rest.
    encryption_key: str = "Zm5tcy1kZXYtb25seS1lbmNyeXB0aW9uLWtleS0wMDA="
    access_token_minutes: int = 60

    # Security controls
    login_rate_limit_per_minute: int = 10
    operation_rate_limit_per_minute: int = 60
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Background processing
    embedded_worker: bool = Field(
        False, description="Run the job worker as a thread inside the API process (single-process demo mode)."
    )
    worker_poll_seconds: float = 2.0
    scheduler_tick_seconds: float = 15.0
    min_poll_interval_seconds: int = 60
    default_poll_interval_seconds: int = 300
    job_lock_timeout_seconds: int = 600

    # Vendor adapters whose behaviour has not been verified against hardware are
    # blocked from write (provisioning) operations unless this is explicitly enabled.
    allow_unverified_adapter_writes: bool = False

    # Simulator
    simulator_enabled: bool = True

    # GIS: optional tile server. Empty means the map falls back to a schematic coordinate plot.
    map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_attribution: str = "&copy; OpenStreetMap contributors"

    # Seeded bootstrap admin (development only; the seed command prints these).
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "ChangeMe!2026"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def validate_production(self) -> list[str]:
        problems = []
        if self.environment == "production":
            if self.jwt_secret.startswith("dev-only"):
                problems.append("FNMS_JWT_SECRET must be set in production")
            if self.encryption_key == Settings.model_fields["encryption_key"].default:
                problems.append("FNMS_ENCRYPTION_KEY must be set in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
