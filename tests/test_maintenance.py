"""Seeded example bookings and the 24-hour demo reset."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app import scheduling as sch
from app.clinic import SERVICES_BY_ID, hours_for
from app.clock import FixedClock
from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.maintenance import reset_all, run_maintenance
from app.models import Booking, ChatMessage, ChatSession
from app.seed import seed_demo_bookings

from .conftest import NOW

SETTINGS = Settings(retention_hours=24, seed_demo_data=True)


def _bookings(db) -> list[Booking]:
    return list(db.scalars(select(Booking).order_by(Booking.start)))


def test_seed_fills_current_and_next_week_without_overlaps(db):
    count = seed_demo_bookings(db, FixedClock(NOW))
    bookings = _bookings(db)
    assert count == len(bookings) > 20

    monday = date(2026, 11, 2)
    days = {b.start.date() for b in bookings}
    assert min(days) >= monday and max(days) < monday + timedelta(days=14)
    assert all(d.weekday() != 6 for d in days)  # never on Sunday

    for b in bookings:
        opens, closes = hours_for(b.start.date())
        assert b.start.time() >= opens and b.end.time() <= closes
        assert b.end - b.start == timedelta(minutes=SERVICES_BY_ID[b.service_id].duration_min)
        assert b.source == "seed" and b.status == "confirmed"

    for a, b in zip(bookings, bookings[1:]):
        if a.start.date() == b.start.date():
            assert a.end <= b.start, f"{a.code} overlaps {b.code}"


def test_seed_is_deterministic_for_a_given_week(tmp_path):
    plans = []
    for name in ("a", "b"):
        engine = make_engine(f"sqlite:///{tmp_path / name}.db")
        init_db(engine)
        with make_session_factory(engine)() as db:
            seed_demo_bookings(db, FixedClock(NOW))
            plans.append([(b.start, b.service_id, b.patient_name, b.code) for b in _bookings(db)])
        engine.dispose()
    assert plans[0] == plans[1]


def test_maintenance_deletes_only_data_older_than_retention(session_factory):
    clock = FixedClock(NOW)
    with session_factory() as db:
        for hours_ago, name in ((25, "Vechi Pacient"), (1, "Nou Pacient")):
            sch.create_booking(
                db,
                sch.NewBooking(
                    service=SERVICES_BY_ID["consultatie"],
                    start=datetime(2026, 11, 6, 10 + hours_ago % 5),
                    name=name,
                    phone="0712 345 678",
                    source="ai",
                ),
                now=NOW,
                created_at=clock.utc_naive() - timedelta(hours=hours_ago),
            )
        old = ChatSession(id="old", created_at=clock.utc_naive() - timedelta(hours=30), last_seen_at=clock.utc_naive())
        old.messages.append(ChatMessage(role="user", content="Bună", created_at=clock.utc_naive()))
        db.add_all([old, ChatSession(id="new", created_at=clock.utc_naive(), last_seen_at=clock.utc_naive())])
        db.commit()

    report = run_maintenance(session_factory, clock, Settings(seed_demo_data=False))

    assert (report.deleted_bookings, report.deleted_sessions) == (1, 1)
    with session_factory() as db:
        assert [b.patient_name for b in _bookings(db)] == ["Nou Pacient"]
        assert db.scalar(select(func.count()).select_from(ChatMessage)) == 0  # cascaded
        assert [s.id for s in db.scalars(select(ChatSession))] == ["new"]


def test_seeds_are_recreated_after_they_expire(session_factory):
    clock = FixedClock(NOW)
    first = run_maintenance(session_factory, clock, SETTINGS)
    assert first.seeded > 0

    again = run_maintenance(session_factory, clock, SETTINGS)  # idempotent while seeds are fresh
    assert again.seeded == 0 and not again.changed

    clock.advance(hours=25)
    later = run_maintenance(session_factory, clock, SETTINGS)
    assert later.deleted_bookings == first.seeded
    assert later.seeded > 0
    with session_factory() as db:
        assert all(b.created_at == clock.utc_naive() for b in _bookings(db))


def test_cancelled_seed_stays_cancelled_until_the_daily_reset(session_factory):
    clock = FixedClock(NOW)
    run_maintenance(session_factory, clock, SETTINGS)
    with session_factory() as db:
        upcoming = next(b for b in _bookings(db) if b.end > NOW)
        sch.cancel_booking(db, upcoming, by="admin", now=NOW, cancelled_at=clock.utc_naive())
    run_maintenance(session_factory, clock, SETTINGS)
    with session_factory() as db:
        assert db.get(Booking, upcoming.id).status == "cancelled"


def test_reset_wipes_everything_and_reseeds(session_factory):
    clock = FixedClock(NOW)
    run_maintenance(session_factory, clock, SETTINGS)
    report = reset_all(session_factory, clock, SETTINGS)
    assert report.deleted_bookings == report.seeded > 0
