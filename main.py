"""
DebrisLink — FastAPI application entry point
---------------------------------------------
Wires the auth and trips routers onto a single ASGI app, ensures the SQLite
schema exists on startup, and guarantees the certificate storage directory
is on disk before the first request lands.

Run:
    uvicorn main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.connection import IS_SQLITE, get_db, init_db
from routers import auth as auth_router
from routers import trips as trips_router
from routers import verify as verify_router
from routers import webhooks as webhooks_router
from services.whatsapp_service import close_whatsapp_client


# ---------------------------------------------------------------------------
# Filesystem bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
CERTIFICATE_DIR = PROJECT_ROOT / "storage" / "certificates"


# ---------------------------------------------------------------------------
# Lifespan: replaces the deprecated @app.on_event("startup") pattern.
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    CERTIFICATE_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield
    # --- Shutdown: gracefully close the shared httpx client. ---
    await close_whatsapp_client()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="DebrisLink API",
    description=(
        "Backend for India's C&D waste logistics platform. "
        "Handles builder/driver onboarding, trip dispatch, and automated "
        "EPR Compliance Certificate generation."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(auth_router.router)
app.include_router(trips_router.router)
app.include_router(webhooks_router.router)
app.include_router(verify_router.router)


@app.get("/", tags=["meta"])
def root() -> dict:
    """Trivial liveness probe (no DB touch)."""
    return {
        "service": "DebrisLink API",
        "version": app.version,
        "status": "ok",
    }


@app.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)) -> JSONResponse:
    """
    Production health check: round-trips a `SELECT 1` to the database.
    Returns 503 if the DB is unreachable so Render/Railway take the
    instance out of rotation instead of routing traffic to a dead pod.
    """
    db_ok = True
    db_error: str | None = None
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — surface any failure mode
        db_ok = False
        db_error = str(exc)[:200]

    body: dict = {
        "status": "ok" if db_ok else "degraded",
        "version": app.version,
        "db": {
            "ok": db_ok,
            "dialect": "sqlite" if IS_SQLITE else "postgresql",
        },
    }
    if db_error:
        body["db"]["error"] = db_error

    return JSONResponse(
        content=body,
        status_code=200 if db_ok else 503,
    )
