"""Error taxonomy (§16) + persistence of error records."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ErrorRecord
from app.domain.enums import ErrorCategory
from app.utils import dumps


class AppError(Exception):
    category: ErrorCategory = ErrorCategory.UNKNOWN_ERROR
    status_code = 400

    def __init__(self, message: str, category: ErrorCategory | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if category:
            self.category = category
        if status_code:
            self.status_code = status_code


class NotFoundError(AppError):
    category = ErrorCategory.VALIDATION_ERROR
    status_code = 404


class ValidationFailed(AppError):
    category = ErrorCategory.VALIDATION_ERROR
    status_code = 422


class TransitionError(AppError):
    category = ErrorCategory.VALIDATION_ERROR
    status_code = 409


class PermissionDenied(AppError):
    category = ErrorCategory.AUTH_ERROR
    status_code = 403


class RateLimited(AppError):
    category = ErrorCategory.RATE_LIMIT
    status_code = 429

    def __init__(self, message: str, wait_seconds: int):
        super().__init__(message)
        self.wait_seconds = wait_seconds


class SubmissionError(AppError):
    category = ErrorCategory.SUBMISSION_ERROR


RETRYABLE = {ErrorCategory.NETWORK_ERROR, ErrorCategory.PROXY_ERROR, ErrorCategory.SERVER_ERROR,
             ErrorCategory.DATABASE_ERROR, ErrorCategory.RATE_LIMIT}


def classify_exception(exc: BaseException) -> ErrorCategory:
    if isinstance(exc, AppError):
        return exc.category
    name = type(exc).__name__
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return ErrorCategory.NETWORK_ERROR
    if "sqlalchemy" in type(exc).__module__ or "asyncpg" in type(exc).__module__:
        return ErrorCategory.DATABASE_ERROR
    if name in ("ValueError", "ValidationError", "TypeError"):
        return ErrorCategory.VALIDATION_ERROR
    return ErrorCategory.UNKNOWN_ERROR


async def record_error(db: AsyncSession, category: ErrorCategory | str, message: str, *, component: str | None = None,
                       session_id: str | None = None, case_id: str | None = None, submission_id: str | None = None,
                       operator: str | None = None, provider: str | None = None, details: dict | None = None) -> ErrorRecord:
    rec = ErrorRecord(category=str(getattr(category, "value", category)), message=message[:4000], component=component,
                      session_id=session_id, case_id=case_id, submission_id=submission_id, operator=operator,
                      provider=provider, details_json=dumps(details) if details else None,
                      occurred_at=datetime.now(timezone.utc))
    db.add(rec)
    return rec
