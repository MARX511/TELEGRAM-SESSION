"""Application settings. Secrets come from the environment / .env, never from code."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CapacitySettings(BaseSettings):
    """Capacity model (§39): configurable limits, measured by benchmarks, never a fixed 'max'."""

    model_config = SettingsConfigDict(env_prefix="CAPACITY_", extra="ignore")

    session_check_concurrency: int = Field(5, ge=1, le=64)
    worker_count: int = Field(2, ge=1, le=32)
    queue_max: int = Field(10_000, ge=10)
    task_timeout_seconds: int = Field(60, ge=1)
    max_retries: int = Field(3, ge=0)
    backoff_base_seconds: float = Field(2.0, ge=0.001)
    backoff_max_seconds: float = Field(300.0, ge=0.001)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "staging", "production"] = "local"
    app_secret_key: str = "change-me-32-bytes-min-change-me-32-bytes"
    app_debug: bool = False
    app_name: str = "Telegram Session & Legal Reporting Platform"

    # Dashboard branding and copyright (shown on the login screen and the settings page only).
    brand_name_ar: str = "منصة البلاغات القانونية"
    brand_name_en: str = "TG Legal Platform"
    copyright_owner_ar: str = "مرتضى أبو زينب"
    copyright_owner_en: str = "Murtada Abu Zainab"

    database_url: str = "postgresql+asyncpg://tglegal:tglegal@localhost:5432/tglegal"
    redis_url: str | None = None

    sessions_root: Path = Path("./sessions")
    session_file_encryption_key: str | None = None
    evidence_root: Path = Path("./evidence_store")
    backup_root: Path = Path("./backups")
    export_root: Path = Path("./exports")

    telegram_provider: Literal["simulation", "telethon"] = "simulation"
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None

    quarantine_failure_threshold: int = Field(3, ge=1)

    submission_email_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    capacity: CapacitySettings = Field(default_factory=CapacitySettings)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_dirs(self) -> None:
        for sub in ("active", "disabled", "quarantined"):
            (self.sessions_root / sub).mkdir(parents=True, exist_ok=True)
        for p in (self.evidence_root, self.backup_root, self.export_root):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
