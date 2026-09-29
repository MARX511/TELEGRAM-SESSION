"""Role-based access control (§28). Permission matrix is data; endpoints declare the permission they need."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.db.models import User
from app.domain.enums import Role
from app.security.auth import get_current_user

P_SESSIONS_READ = "sessions:read"
P_SESSIONS_WRITE = "sessions:write"
P_SESSIONS_CHECK = "sessions:check"
P_PROXIES_READ = "proxies:read"
P_PROXIES_WRITE = "proxies:write"
P_TARGETS_READ = "targets:read"
P_TARGETS_WRITE = "targets:write"
P_CASES_READ = "cases:read"
P_CASES_WRITE = "cases:write"
P_CASES_APPROVE = "cases:approve"
P_EVIDENCE_READ = "evidence:read"
P_EVIDENCE_WRITE = "evidence:write"
P_SUBMISSIONS_READ = "submissions:read"
P_SUBMISSIONS_WRITE = "submissions:write"
P_SUBMISSIONS_EXECUTE = "submissions:execute"
P_REPORTS_EXPORT = "reports:export"
P_AUDIT_READ = "audit:read"
P_USERS_MANAGE = "users:manage"
P_BACKUPS_MANAGE = "backups:manage"
P_SETTINGS_MANAGE = "settings:manage"
P_MONITORING_READ = "monitoring:read"

ALL_PERMISSIONS = {v for k, v in globals().items() if k.startswith("P_")}

_READ = {P_SESSIONS_READ, P_PROXIES_READ, P_TARGETS_READ, P_CASES_READ, P_EVIDENCE_READ, P_SUBMISSIONS_READ,
         P_MONITORING_READ}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    Role.ADMIN.value: set(ALL_PERMISSIONS),
    Role.OPERATOR.value: _READ | {P_SESSIONS_WRITE, P_SESSIONS_CHECK, P_PROXIES_WRITE, P_TARGETS_WRITE,
                                  P_CASES_WRITE, P_EVIDENCE_WRITE, P_SUBMISSIONS_WRITE, P_SUBMISSIONS_EXECUTE,
                                  P_REPORTS_EXPORT},
    Role.REVIEWER.value: _READ | {P_CASES_APPROVE, P_CASES_WRITE, P_REPORTS_EXPORT, P_AUDIT_READ},
    Role.AUDITOR.value: _READ | {P_AUDIT_READ, P_REPORTS_EXPORT},
    Role.VIEWER.value: set(_READ),
}


def has_permission(user: User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.role, set())


def require(permission: str):
    async def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Permission '{permission}' required (role={user.role})")
        return user

    return _dep
