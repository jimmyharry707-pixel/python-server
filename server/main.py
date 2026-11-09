"""Baymax central server - FastAPI application entry point.

Run locally (from the repo root, not from server/):
    uvicorn server.main:app --reload --port 8000
Or on Windows from server/:
    .\run_dev.ps1

Implements the desktop client contract under ``/api/v1`` plus licensing,
profiles, fleet monitoring, releases and an admin dashboard.
"""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure webfonts (Tabler icons) are not served as text/plain on some hosts.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("image/svg+xml", ".svg")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import get_settings
from .migrations import run_migrations
from .routers import (
    admin,
    announcements,
    applications,
    auth,
    fleet,
    interviews,
    license,
    licensing,
    profiles_admin,
    releases,
    review_scans,
    sync,
    team,
    training,
    users,
    work_plans,
)
from .seed import bootstrap_admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("baymax.server")

_settings = get_settings()
_WEB_DIST = Path(__file__).parent / "web" / "dist"
_AVATAR_DIR = Path(__file__).parent / "data" / "avatars"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Running migrations...")
    run_migrations()
    bootstrap_admin()
    # Fail fast on a misconfigured signing key so license activation can't
    # 500 later. In production this raises if BAYMAX_LICENSE_SIGNING_KEY is unset.
    from .licensing import get_signing_private_key

    get_signing_private_key()
    # Daily review scans run one at a time on a background thread.
    from .scan import worker as scan_worker

    scan_worker.start()
    db_url = (_settings.database_url or "").lower()
    if db_url.startswith("sqlite") and getattr(_settings, "is_production", False):
        logger.warning(
            "Database is SQLite in production. Concurrent multi-client sync writers "
            "will serialize poorly — set BAYMAX_DATABASE_URL to Postgres."
        )
    logger.info("Baymax server %s ready (env=%s)", __version__, _settings.environment)
    yield


app = FastAPI(title="Baymax Central Server", version=__version__, lifespan=lifespan)

_cors_origins = _settings.cors_origin_list
# Never combine allow_credentials=True with origin "*".
_allow_creds = "*" not in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers under /api/v1
_api = _settings.api_prefix
for module in (
    auth,
    users,
    applications,
    interviews,
    sync,
    license,
    licensing,
    fleet,
    releases,
    profiles_admin,
    admin,
    team,
    training,
    announcements,
    work_plans,
    review_scans,
):
    app.include_router(module.router, prefix=_api)


@app.get("/health")
def health() -> dict:
    """Liveness probe. The desktop client checks this (without the api prefix)."""
    return {"status": "ok", "version": __version__, "environment": _settings.environment}


@app.get("/metrics")
def metrics() -> Response:
    """Minimal Prometheus-style metrics for uptime/monitoring scrapers."""
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from .database import SessionLocal
    from .models import Application, Client, ErrorReport, Interview, User

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        users = db.execute(select(func.count()).select_from(User)).scalar_one()
        clients = db.execute(select(func.count()).select_from(Client)).scalar_one()
        online = db.execute(
            select(func.count())
            .select_from(Client)
            .where(Client.last_seen >= now - timedelta(minutes=10))
        ).scalar_one()
        apps = db.execute(select(func.count()).select_from(Application)).scalar_one()
        interviews = db.execute(select(func.count()).select_from(Interview)).scalar_one()
        errors_24h = db.execute(
            select(func.count())
            .select_from(ErrorReport)
            .where(ErrorReport.created_at >= now - timedelta(hours=24))
        ).scalar_one()
    finally:
        db.close()

    lines = [
        "# HELP baymax_up Server up.",
        "# TYPE baymax_up gauge",
        "baymax_up 1",
        f"baymax_users_total {users}",
        f"baymax_clients_total {clients}",
        f"baymax_clients_online {online}",
        f"baymax_applications_total {apps}",
        f"baymax_interviews_total {interviews}",
        f"baymax_errors_24h {errors_24h}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain")


_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
}


@app.get("/")
def root() -> FileResponse:
    """CareerCraft web app (login/dashboard gate)."""
    return _web_file("index.html")


@app.get("/meta")
def meta() -> JSONResponse:
    return JSONResponse(
        {
            "service": "baymax-central-server",
            "version": __version__,
            "api": _api,
            "app": "/",
            "docs": "/docs",
        }
    )


def _web_file(name: str) -> FileResponse:
    """Serve a built web HTML page with no-cache headers."""
    path = (_WEB_DIST / name).resolve()
    root = _WEB_DIST.resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail="Web UI not built — run: cd server/web && npm install && npm run build",
        )
    return FileResponse(path, headers=_NO_CACHE, media_type="text/html")


_WEB_PAGES = (
    "login",
    "dashboard",
    "users",
    "profiles",
    "licenses",
    "sync",
    "errors",
    "telemetry",
    "interviews",
    "performance",
    "workplan",
    "colleagues",
    "training",
    "training-editor",
    "announcements",
    "notifications",
)


def _add_web_page(page: str) -> None:
    def _handler() -> FileResponse:
        return _web_file("index.html")

    app.add_api_route(f"/{page}", _handler, methods=["GET", "HEAD"], name=f"web_{page}")
    app.add_api_route(
        f"/{page}.html",
        _handler,
        methods=["GET", "HEAD"],
        name=f"web_{page}_html",
    )


for _page in _WEB_PAGES:
    _add_web_page(_page)


@app.get("/react")
@app.get("/react/{path:path}")
def react_app(path: str = "") -> FileResponse:
    """Compatibility path for the React SPA preview URL."""
    return _web_file("index.html")


@app.api_route("/admin", methods=["GET", "HEAD"])
def admin_redirect() -> RedirectResponse:
    """Old admin URL → primary app login."""
    return RedirectResponse(url="/login", status_code=302)


@app.api_route("/stage", methods=["GET", "HEAD"])
@app.api_route("/stage/", methods=["GET", "HEAD"])
def stage_root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=302)


@app.api_route("/stage/{path:path}", methods=["GET", "HEAD"])
def stage_path_redirect(path: str) -> RedirectResponse:
    """Compat redirects from the former /stage prototype paths."""
    raw = (path or "").lstrip("/")
    if raw.startswith("assets/") or raw in (
        "favicon.ico",
        "careercraft_icon.png",
    ):
        return RedirectResponse(url=f"/{raw}", status_code=302)
    stem = raw[:-5] if raw.endswith(".html") else raw
    if stem in _WEB_PAGES:
        return RedirectResponse(url=f"/{stem}", status_code=302)
    if not stem or stem == "index":
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url=f"/{raw}", status_code=302)


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    from fastapi import HTTPException

    path = _WEB_DIST / "favicon.ico"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers=_NO_CACHE)


@app.get("/careercraft_icon.png")
def brand_icon() -> FileResponse:
    from fastapi import HTTPException

    path = _WEB_DIST / "careercraft_icon.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers=_NO_CACHE)


# User avatar uploads (created on first upload).
_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/uploads/avatars",
    StaticFiles(directory=str(_AVATAR_DIR)),
    name="avatars",
)

# Built web UI assets (JS/CSS/fonts). HTML is served by explicit routes above.
_WEB_ASSETS = _WEB_DIST / "assets"
if _WEB_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_WEB_ASSETS)), name="web_assets")


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str) -> FileResponse:
    """Serve React Router paths while preserving JSON 404s for API typos."""
    if path == "api" or path.startswith("api/"):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")
    return _web_file("index.html")
