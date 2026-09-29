"""Sandbox / simulation provider (default). Deterministic outcomes derived from the file so tests and benchmarks
are repeatable, with filename hints to force specific scenarios:
  *banned*  -> BANNED      *expired* -> EXPIRED     *invalid* -> INVALID
  *flood*   -> RATE_LIMIT  *netfail* -> NETWORK_ERROR  *proxyfail* -> PROXY_ERROR
Anything that is not a recognised session database is INVALID."""
from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path

from app.domain.enums import ErrorCategory, SessionStatus
from app.telegram.session_files import inspect_session_file
from app.telegram.validator import CheckResult, ProxySpec


class SimulationValidator:
    provider = "simulation"

    def __init__(self, latency_ms: int = 5, flood_wait_seconds: int = 30):
        self.latency_ms = latency_ms
        self.flood_wait_seconds = flood_wait_seconds

    async def check(self, file_path: Path, proxy: ProxySpec | None = None) -> CheckResult:
        t0 = time.perf_counter()
        await asyncio.sleep(self.latency_ms / 1000)
        name = file_path.name.lower()
        info = inspect_session_file(file_path)
        lat = int((time.perf_counter() - t0) * 1000)

        def fail(status: SessionStatus, cat: ErrorCategory, msg: str, wait: int | None = None) -> CheckResult:
            return CheckResult(status=status, success=False, latency_ms=lat, error_category=cat, error_message=msg,
                               server_wait_seconds=wait, details={"format": info.fmt, "simulated": True})

        if not info.exists:
            return fail(SessionStatus.UNAVAILABLE, ErrorCategory.SESSION_ERROR, "session file missing")
        if info.fmt in ("not_sqlite", "unknown"):
            return fail(SessionStatus.INVALID, ErrorCategory.VALIDATION_ERROR, info.error or "unrecognised session file")
        if "proxyfail" in name:
            return fail(SessionStatus.CHECK_FAILED, ErrorCategory.PROXY_ERROR, "simulated proxy connection failure")
        if "netfail" in name:
            return fail(SessionStatus.CHECK_FAILED, ErrorCategory.NETWORK_ERROR, "simulated network failure")
        if "flood" in name:
            return fail(SessionStatus.CHECK_FAILED, ErrorCategory.RATE_LIMIT,
                        f"simulated FloodWait: server asked to wait {self.flood_wait_seconds}s",
                        wait=self.flood_wait_seconds)
        if "banned" in name:
            return fail(SessionStatus.BANNED, ErrorCategory.AUTH_ERROR, "simulated: account deactivated/banned")
        if "expired" in name:
            return fail(SessionStatus.EXPIRED, ErrorCategory.AUTH_ERROR, "simulated: session revoked / auth key unregistered")
        if "invalid" in name:
            return fail(SessionStatus.INVALID, ErrorCategory.SESSION_ERROR, "simulated: session not authorised")

        digest = hashlib.sha256(file_path.name.encode()).digest()
        fake_id = 100_000_000 + int.from_bytes(digest[:4], "big") % 900_000_000
        return CheckResult(
            status=SessionStatus.VALID, success=True, latency_ms=lat, telegram_id=info.user_id or fake_id,
            username=f"sim_{digest[:3].hex()}", phone_masked="+9*******" + f"{digest[3] % 100:02d}",
            display_name="Simulated Account", details={"format": info.fmt, "dc_id": info.dc_id, "simulated": True},
        )
