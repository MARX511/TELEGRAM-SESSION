"""Validation provider interface (§2). Providers ONLY verify that a session is authorised and healthy.
No provider performs any action on behalf of the account (no messaging, no reporting, no joining)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.domain.enums import ErrorCategory, SessionStatus


@dataclass
class ProxySpec:
    host: str
    port: int
    protocol: str
    username: str | None = None
    password: str | None = None


@dataclass
class CheckResult:
    status: SessionStatus
    success: bool
    latency_ms: int = 0
    telegram_id: int | None = None
    username: str | None = None
    phone_masked: str | None = None
    display_name: str | None = None
    error_category: ErrorCategory | None = None
    error_message: str | None = None
    server_wait_seconds: int | None = None   # FloodWait told by the server; we wait, we never bypass (§7)
    details: dict = field(default_factory=dict)


class SessionValidator(Protocol):
    provider: str

    async def check(self, file_path: Path, proxy: ProxySpec | None = None) -> CheckResult: ...


def get_validator(provider: str | None = None) -> SessionValidator:
    from app.config import get_settings

    provider = provider or get_settings().telegram_provider
    if provider == "telethon":
        from app.telegram.telethon_adapter import TelethonValidator

        return TelethonValidator()
    from app.telegram.simulation import SimulationValidator

    return SimulationValidator()
