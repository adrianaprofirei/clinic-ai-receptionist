"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .clock import Clock
from .config import Settings, with_generated_secrets
from .db import init_db, make_engine, make_session_factory

log = logging.getLogger("zambet")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    _configure_logging(settings.log_level)
    settings = with_generated_secrets(settings)

    engine = make_engine(settings.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    clock = clock or Clock(settings.timezone)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("Clinica Zâmbet demo %s starting (AI mode: %s)", __version__, settings.ai_configured)
        tasks: list[asyncio.Task] = []
        yield
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        engine.dispose()

    app = FastAPI(
        title="Clinica Zâmbet – AI receptionist demo",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.api_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.api_docs else None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.clock = clock

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok", "ai": settings.ai_configured}

    return app

