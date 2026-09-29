from __future__ import annotations

from fastapi import Request

from app.db.engine import get_db, get_session_factory  # noqa: F401
from app.security.auth import get_current_user, get_current_user_optional  # noqa: F401
from app.security.rbac import require  # noqa: F401
from app.workers.queue import JobQueue


def get_queue(request: Request) -> JobQueue:
    q = getattr(request.app.state, "queue", None)
    if q is None:
        q = JobQueue(get_session_factory())
        request.app.state.queue = q
    return q


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
