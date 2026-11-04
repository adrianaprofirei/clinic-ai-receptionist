"""The daily demo reset: delete data older than the retention period, re-seed.

Runs every MAINTENANCE_INTERVAL_MINUTES inside the app, and can be run by hand:

    python -m app.maintenance            # one cleanup pass
    python -m app.maintenance --reset    # wipe everything and re-seed (before a demo)
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session, sessionmaker

from .clock import Clock
from .config import Settings
from .models import Booking, ChatSession, UsageDay
from .seed import seed_demo_bookings

log = logging.getLogger("zambet.maintenance")


@dataclass(frozen=True)
class MaintenanceReport:
    deleted_bookings: int = 0
    deleted_sessions: int = 0
    seeded: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.deleted_bookings or self.seeded)


def run_maintenance(session_factory: sessionmaker[Session], clock: Clock, settings: Settings) -> MaintenanceReport:
    cutoff = clock.utc_naive() - timedelta(hours=settings.retention_hours)
    with session_factory() as db:
        # Slot claims and chat messages go with them (ON DELETE CASCADE).
        deleted_bookings = db.execute(delete(Booking).where(Booking.created_at < cutoff)).rowcount or 0
        deleted_sessions = db.execute(delete(ChatSession).where(ChatSession.created_at < cutoff)).rowcount or 0
        db.execute(delete(UsageDay).where(UsageDay.day < clock.now_local().date() - timedelta(days=7)))
        db.commit()

        seeded = 0
        if settings.seed_demo_data and not db.scalar(select(exists().where(Booking.source == "seed"))):
            seeded = seed_demo_bookings(db, clock)

    report = MaintenanceReport(deleted_bookings, deleted_sessions, seeded)
    if report.changed or deleted_sessions:
        log.info(
            "Maintenance: deleted %d bookings and %d chat sessions, seeded %d example bookings",
            deleted_bookings, deleted_sessions, seeded,
        )
    return report


def reset_all(session_factory: sessionmaker[Session], clock: Clock, settings: Settings) -> MaintenanceReport:
    """Wipe every booking and chat, then seed a fresh calendar."""
    with session_factory() as db:
        deleted_bookings = db.execute(delete(Booking)).rowcount or 0
        deleted_sessions = db.execute(delete(ChatSession)).rowcount or 0
        db.commit()
        seeded = seed_demo_bookings(db, clock) if settings.seed_demo_data else 0
    return MaintenanceReport(deleted_bookings, deleted_sessions, seeded)


def main() -> None:  # pragma: no cover - thin CLI wrapper
    from .db import init_db, make_engine, make_session_factory

    parser = argparse.ArgumentParser(description="Clean up or reset the demo data.")
    parser.add_argument("--reset", action="store_true", help="delete all bookings and chats, then re-seed")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    clock = Clock(settings.timezone)
    report = (reset_all if args.reset else run_maintenance)(factory, clock, settings)
    print(
        f"deleted {report.deleted_bookings} bookings, {report.deleted_sessions} chat sessions; "
        f"seeded {report.seeded} example bookings"
    )


if __name__ == "__main__":  # pragma: no cover
    main()
