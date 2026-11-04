"""SQLAlchemy engine and session setup (SQLite by default)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _sqlite_path(url: str) -> str | None:
    if not url.startswith("sqlite") or "///" not in url:
        return None
    path = url.split("///", 1)[1].split("?", 1)[0]
    return None if path in {"", ":memory:"} else path


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    kwargs: dict = {}
    if is_sqlite:
        # FastAPI runs sync endpoints in a thread pool; one connection may be
        # used by several threads in sequence (never concurrently).
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}
        path = _sqlite_path(url)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db(engine: Engine) -> None:
    """Create tables. Demo data is ephemeral, so no migration tool is needed."""
    from . import models  # noqa: F401  (registers the tables)

    Base.metadata.create_all(engine)
