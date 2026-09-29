"""FastAPI application factory."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.config import get_settings
from app.db.engine import get_session_factory, reset_engine
from app.logging_config import configure_logging, get_logger
from app.security.auth import ensure_bootstrap_admin
from app.services.errors import AppError
from app.services.reasons import seed_reasons
from app.services.templates import seed_templates
from app.web.router import router as web_router
from app.workers.handlers import HANDLERS
from app.workers.queue import JobQueue
from app.workers.worker import WorkerPool

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.app_debug, json_output=settings.app_env != "local")
    settings.ensure_dirs()
    factory = get_session_factory()
    async with factory() as db:
        await seed_reasons(db)
        await seed_templates(db)
        pw = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
        if pw:
            created = await ensure_bootstrap_admin(db, os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin"), pw)
            if created:
                log.info("bootstrap_admin_created", username=created.username)
        await db.commit()
    app.state.queue = JobQueue(factory, settings)
    app.state.workers = None
    if os.environ.get("WORKERS_ENABLED", "true").lower() in ("1", "true", "yes"):
        app.state.workers = WorkerPool(app.state.queue, HANDLERS, settings=settings)
        await app.state.workers.start()
    log.info("app_started", env=settings.app_env, provider=settings.telegram_provider)
    try:
        yield
    finally:
        if app.state.workers:
            await app.state.workers.stop()
        await reset_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json")

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "category": exc.category.value})

    @app.get("/healthz", tags=["system"])
    async def healthz():
        return {"status": "ok", "env": settings.app_env}

    app.include_router(api_router)
    app.include_router(web_router)
    static_dir = os.path.join(os.path.dirname(__file__), "web", "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


app = create_app()
