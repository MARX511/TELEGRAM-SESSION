"""Proxy registry and connectivity checks (§4). Routing / network isolation only."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Proxy
from app.domain.enums import AuditAction, ErrorCategory, ProxyStatus
from app.security.secrets import get_secret
from app.services.audit import record_audit
from app.services.errors import NotFoundError, ValidationFailed, record_error
from app.telegram.validator import ProxySpec


async def create_proxy(db: AsyncSession, *, host: str, port: int, protocol: str, name: str | None = None,
                       username: str | None = None, secret_ref: str | None = None, actor: str | None = None) -> Proxy:
    if not (1 <= port <= 65535):
        raise ValidationFailed("port out of range")
    p = Proxy(host=host, port=port, protocol=protocol, name=name, username=username, secret_ref=secret_ref)
    db.add(p)
    await db.flush()
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="proxy", entity_id=p.id,
                       details={"op": "create", "host": host, "port": port, "protocol": protocol})
    return p


async def get_proxy(db: AsyncSession, proxy_id: str) -> Proxy:
    p = await db.get(Proxy, proxy_id)
    if not p:
        raise NotFoundError("proxy not found")
    return p


async def list_proxies(db: AsyncSession) -> list[Proxy]:
    return list((await db.execute(select(Proxy).order_by(Proxy.created_at))).scalars().all())


def to_spec(p: Proxy | None) -> ProxySpec | None:
    if p is None or not p.enabled:
        return None
    return ProxySpec(host=p.host, port=p.port, protocol=p.protocol, username=p.username,
                     password=get_secret(p.secret_ref))


async def check_proxy(db: AsyncSession, proxy: Proxy, *, timeout: float = 5.0, actor: str | None = None) -> Proxy:
    """TCP reachability + latency. This is a connectivity probe, not traffic through the proxy."""
    t0 = time.perf_counter()
    err: str | None = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(proxy.host, proxy.port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        proxy.status = ProxyStatus.UP.value
        proxy.latency_ms = int((time.perf_counter() - t0) * 1000)
        proxy.last_error = None
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        proxy.status = ProxyStatus.DOWN.value
        proxy.latency_ms = None
        proxy.last_error = err
        await record_error(db, ErrorCategory.PROXY_ERROR, err, component="proxies", details={"proxy_id": proxy.id})
    proxy.last_check = datetime.now(timezone.utc)
    await record_audit(db, AuditAction.PROXY_CHECKED, actor=actor, entity_type="proxy", entity_id=proxy.id,
                       result=proxy.status, reason=err)
    return proxy
