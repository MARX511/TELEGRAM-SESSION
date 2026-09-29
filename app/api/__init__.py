from fastapi import APIRouter

from app.api import auth, cases, evidence, proxies, sessions, submissions, system, targets

api_router = APIRouter(prefix="/api/v1")
for r in (auth.router, sessions.router, sessions.groups_router, sessions.accounts_router, proxies.router, targets.router,
          cases.router, cases.reasons_router, cases.templates_router, evidence.router, submissions.router,
          system.exports_router, system.audit_router, system.errors_router, system.monitoring_router,
          system.jobs_router, system.backups_router):
    api_router.include_router(r)
