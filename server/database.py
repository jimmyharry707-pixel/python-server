"""SQLAlchemy engine/session setup.

Supports SQLite (dev) and Postgres (prod) transparently based on
``BAYMAX_DATABASE_URL``. Relative SQLite paths are always resolved under the
``server/`` directory so starting from the repo root vs ``server/`` does not
create two different databases.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

_SERVER_DIR = Path(__file__).resolve().parent


def _resolve_database_url(url: str) -> str:
    if not url.startswith("sqlite:///"):
        return url
    raw = url[len("sqlite:///") :]
    path = Path(raw)
    if path.is_absolute():
        return f"sqlite:///{path.as_posix()}"
    return f"sqlite:///{(_SERVER_DIR / path).resolve().as_posix()}"


_settings = get_settings()
_database_url = _resolve_database_url(_settings.database_url)

# SQLite needs a special flag for use across FastAPI's threadpool.
_connect_args = {}
if _database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    _database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
