"""
DebrisLink — Database Connection Module
----------------------------------------
Single source of truth for the SQLAlchemy engine. Behavior:

  * If DATABASE_URL is set in the environment → use it. This is how the
    cloud platform (Render / Railway / Fly / Heroku) wires the managed
    PostgreSQL instance into the container.
  * If DATABASE_URL is unset → fall back to a local SQLite file so
    developers get a zero-config laptop experience.

Cloud quirks handled:
  * Render / Heroku expose `postgres://...` URLs, but SQLAlchemy 2.0
    requires the canonical `postgresql://` scheme — we rewrite the prefix.
  * Managed Postgres instances aggressively recycle idle connections;
    `pool_pre_ping=True` plus a 5-minute `pool_recycle` keeps dropped
    connections from leaking into request handlers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker


# ---------------------------------------------------------------------------
# 1. Resolve the database URL
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
_SQLITE_FALLBACK = f"sqlite:///{BASE_DIR / 'debrislink.db'}"


def _resolve_database_url() -> str:
    """
    Read DATABASE_URL from the environment and normalize cloud quirks.
    Returns the SQLite fallback when unset, so `python simulate_workflow.py`
    keeps working on a laptop with no config.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return _SQLITE_FALLBACK

    # Heroku/Render historically expose `postgres://`; SQLAlchemy 2.0
    # only accepts the canonical `postgresql://` form.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    # Guard: catch the common mistake of pasting a Supabase/Render *API*
    # URL (https://...) instead of the database connection string.
    _VALID_SCHEMES = ("postgresql://", "postgres://", "sqlite://")
    if not any(url.startswith(s) for s in _VALID_SCHEMES):
        raise ValueError(
            f"DATABASE_URL must start with 'postgresql://' (got: {url[:40]!r}). "
            "In Supabase: Settings → Database → Connection string → URI tab. "
            "In Render: Settings → Environment → DATABASE_URL."
        )

    return url


SQLALCHEMY_DATABASE_URL = _resolve_database_url()
IS_SQLITE = SQLALCHEMY_DATABASE_URL.startswith("sqlite")


# ---------------------------------------------------------------------------
# 2. Build the engine — dialect-aware kwargs
# ---------------------------------------------------------------------------
def _build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # SQLite (local dev): single file, FastAPI threading caveat.
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            echo=False,
            future=True,
        )

    # PostgreSQL (cloud production): pooled connections with pre-ping
    # so dropped or recycled connections don't bleed into requests.
    # Pool sizing is env-overridable so you can tune per plan size.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        pool_recycle=int(os.environ.get("DB_POOL_RECYCLE", "300")),
        echo=False,
        future=True,
    )


engine = _build_engine(SQLALCHEMY_DATABASE_URL)


# ---------------------------------------------------------------------------
# 3. Session factory + declarative base
# ---------------------------------------------------------------------------
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)

Base = declarative_base()


# ---------------------------------------------------------------------------
# 4. FastAPI dependency
# ---------------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """Yield a session and guarantee close. Use with `Depends(get_db)`."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. Bootstrap helper
# ---------------------------------------------------------------------------
def init_db() -> None:
    """
    Create all tables defined on `Base.metadata`. Idempotent — safe to call
    on every app startup. Once the schema stabilizes, replace this with
    Alembic migrations and keep `init_db()` for the local SQLite path only.
    """
    from database import models  # noqa: F401 — registers models on Base

    Base.metadata.create_all(bind=engine)
