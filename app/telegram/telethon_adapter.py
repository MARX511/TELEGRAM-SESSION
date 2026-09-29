"""Optional real provider (TELEGRAM_PROVIDER=telethon). Scope is strictly limited to: connect, check that the
session is authorised, read the account's own identity, disconnect. FloodWait and other server limits are
reported back as-is so the caller waits the server-defined delay (§7). Nothing else is ever invoked."""
from __future__ import annotations

import time
from pathlib import Path

from app.config import get_settings
from app.domain.enums import ErrorCategory, SessionStatus
from app.telegram.validator import CheckResult, ProxySpec
from app.utils import mask_phone


class TelethonValidator:
    provider = "telethon"

    async def check(self, file_path: Path, proxy: ProxySpec | None = None) -> CheckResult:
        try:
            from telethon import TelegramClient, errors
        except ImportError:  # pragma: no cover
            return CheckResult(status=SessionStatus.CHECK_FAILED, success=False,
                               error_category=ErrorCategory.VALIDATION_ERROR,
                               error_message="telethon is not installed (pip install .[telegram])")
        s = get_settings()
        if not s.telegram_api_id or not s.telegram_api_hash:
            return CheckResult(status=SessionStatus.CHECK_FAILED, success=False,
                               error_category=ErrorCategory.VALIDATION_ERROR,
                               error_message="TELEGRAM_API_ID / TELEGRAM_API_HASH not configured")
        proxy_arg = None
        if proxy:
            import socks  # python-socks

            ptype = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}.get(proxy.protocol)
            if ptype is None:
                return CheckResult(status=SessionStatus.CHECK_FAILED, success=False,
                                   error_category=ErrorCategory.PROXY_ERROR,
                                   error_message=f"unsupported proxy protocol {proxy.protocol}")
            proxy_arg = (ptype, proxy.host, proxy.port, True, proxy.username, proxy.password)

        session_path = str(file_path)
        if session_path.endswith(".session"):
            session_path = session_path[: -len(".session")]
        client = TelegramClient(session_path, s.telegram_api_id, s.telegram_api_hash, proxy=proxy_arg,
                                connection_retries=1, retry_delay=1, timeout=s.capacity.task_timeout_seconds)
        t0 = time.perf_counter()
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return CheckResult(status=SessionStatus.INVALID, success=False,
                                   latency_ms=int((time.perf_counter() - t0) * 1000),
                                   error_category=ErrorCategory.AUTH_ERROR, error_message="session is not authorised")
            me = await client.get_me()
            lat = int((time.perf_counter() - t0) * 1000)
            return CheckResult(status=SessionStatus.VALID, success=True, latency_ms=lat, telegram_id=me.id,
                               username=me.username, phone_masked=mask_phone(getattr(me, "phone", None)),
                               display_name=" ".join(x for x in (me.first_name, me.last_name) if x) or None)
        except errors.FloodWaitError as exc:
            return CheckResult(status=SessionStatus.CHECK_FAILED, success=False, error_category=ErrorCategory.RATE_LIMIT,
                               error_message=f"FloodWait {exc.seconds}s", server_wait_seconds=int(exc.seconds))
        except (errors.UserDeactivatedBanError, errors.UserDeactivatedError, errors.PhoneNumberBannedError) as exc:
            return CheckResult(status=SessionStatus.BANNED, success=False, error_category=ErrorCategory.AUTH_ERROR,
                               error_message=type(exc).__name__)
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.SessionExpiredError,
                errors.AuthKeyInvalidError) as exc:
            return CheckResult(status=SessionStatus.EXPIRED, success=False, error_category=ErrorCategory.AUTH_ERROR,
                               error_message=type(exc).__name__)
        except errors.RPCError as exc:
            return CheckResult(status=SessionStatus.CHECK_FAILED, success=False, error_category=ErrorCategory.SERVER_ERROR,
                               error_message=f"{type(exc).__name__}: {exc}")
        except (ConnectionError, OSError, TimeoutError) as exc:
            cat = ErrorCategory.PROXY_ERROR if proxy else ErrorCategory.NETWORK_ERROR
            return CheckResult(status=SessionStatus.CHECK_FAILED, success=False, error_category=cat,
                               error_message=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(status=SessionStatus.CHECK_FAILED, success=False, error_category=ErrorCategory.UNKNOWN_ERROR,
                               error_message=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
