"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, auth, infrastructure, integrations, monitoring_api, network, onts, provisioning_api, topology_api
from app.config import get_settings
from app.core.errors import DomainError
from app.logging_setup import setup_logging

log = logging.getLogger("fnms.api")

DESCRIPTION = """
Standalone fiber network management API (OLT / ONT / FAT / fiber topology / monitoring / provisioning).

* Authenticate with `POST /api/v1/auth/login`, then send `Authorization: Bearer <token>`.
* Long-running network operations return **202** with a `job_id`; poll `GET /api/v1/jobs/{job_id}`.
* Credentials are write-only: responses expose only `has_<secret>` booleans.
* `/api/v1/integration/v1/*` is the contract intended for a future billing-system integration.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    problems = s.validate_production()
    if problems:
        raise RuntimeError("; ".join(problems))
    stop = None
    if s.embedded_worker:
        from app.jobs.worker import start_embedded

        stop = start_embedded()
        log.info("embedded worker started")
    yield
    if stop:
        stop.set()


def create_app() -> FastAPI:
    setup_logging()
    s = get_settings()
    app = FastAPI(title=f"{s.app_name} API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = resp.headers.get("Cache-Control", "no-store")
        return resp

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.exception_handler(RequestValidationError)
    async def req_validation(_: Request, exc: RequestValidationError):
        errs = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": "Invalid request",
                                                                 "details": errs}})

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error: %s", exc.__class__.__name__)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Internal server error"}})

    prefix = "/api/v1"
    for r in (auth.router, admin.router, network.router, onts.router, infrastructure.router, topology_api.router,
              provisioning_api.router, monitoring_api.router, integrations.router, integrations.integration):
        app.include_router(r, prefix=prefix)

    @app.get("/api/health", tags=["health"])
    def health():
        from sqlalchemy import text

        from app.db import SessionLocal

        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return {"status": "ok", "database": "ok", "embedded_worker": s.embedded_worker}
        finally:
            db.close()

    # Make job handlers importable/registered for enqueue validation and embedded mode
    from app.jobs.worker import load_handlers

    load_handlers()
    return app


app = create_app()
