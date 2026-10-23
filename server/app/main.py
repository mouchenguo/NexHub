"""NexHub ECS 服务端入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .common import client_ip
from .compat import API_VERSION, MIN_CLIENT_VERSION, server_info
from .config import settings
from .routers import account, admin, auth, dashboard, devices, public, shares, tunnels

logger = logging.getLogger("nexhub.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    app.state.started_at = datetime.now(timezone.utc)
    logger.info("NexHub server started, db=%s", settings.db_path)
    yield
    logger.info("NexHub server stopped")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
downloads_dir = settings.base_dir / "downloads"
downloads_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/downloads",
    StaticFiles(directory=str(downloads_dir), html=True),
    name="downloads",
)

for router in (
    auth.router,
    dashboard.router,
    shares.router,
    devices.router,
    tunnels.router,
    account.router,
    public.router,
    admin.router,
):
    app.include_router(router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-NexHub-Server-Version", settings.version)
    response.headers.setdefault("X-NexHub-Api-Version", str(API_VERSION))
    response.headers.setdefault("X-NexHub-Min-Client-Version", MIN_CLIENT_VERSION)
    if request.url.path.startswith("/admin"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    try:
        db.log_event(
            "error",
            "error",
            f"{request.method} {request.url.path} 失败：{exc}",
            ip=client_ip(request),
        )
    except Exception:
        pass
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


@app.get("/api/health")
def health():
    db_ok = True
    try:
        db.query_one("SELECT 1 AS ok")
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        **server_info(),
        "service": "nexhub-server",
        "database": "ok" if db_ok else "error",
        "time": db.utcnow(),
    }


@app.get("/download")
def download():
    return RedirectResponse("/downloads/", status_code=307)


@app.get("/")
def index():
    return RedirectResponse("/admin", status_code=307)
