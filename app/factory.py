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
from .events import EventBus
from .maintenance import run_maintenance

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
    bus = EventBus()

    def maintenance_pass() -> None:
        report = run_maintenance(session_factory, clock, settings)
        if report.changed:
            bus.publish({"type": "reset"})

    async def maintenance_loop(interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await asyncio.to_thread(maintenance_pass)
            except Exception:  # keep the loop alive; the next pass retries
                log.exception("Maintenance pass failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("Clinica Zâmbet demo %s starting (AI mode: %s)", __version__, settings.ai_configured)
        bus.bind(asyncio.get_running_loop())
        await asyncio.to_thread(maintenance_pass)  # clean up and seed before serving
        tasks: list[asyncio.Task] = []
        if settings.maintenance_interval_minutes > 0:
            tasks.append(asyncio.create_task(maintenance_loop(settings.maintenance_interval_minutes * 60)))
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
    app.state.bus = bus

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok", "ai": settings.ai_configured}

    return app

