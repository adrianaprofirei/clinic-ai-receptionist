"""Example bookings so the reception calendar is never empty.

The plan is deterministic per week (seeded RNG), so restarts on the same day
reproduce the same calendar. Seeds go through the normal booking rules, so they
never overlap and always sit inside opening hours.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .clinic import SERVICES
from .clock import Clock
from .scheduling import BookingError, NewBooking, busy_intervals, create_booking, day_window, grid_starts

FIRST_NAMES = (
    "Ioana", "George", "Diana", "Raluca", "Paul", "Victor", "Simona", "Andrei", "Oana", "Larisa",
    "Ștefan", "Maria", "Cristina", "Dan", "Florin", "Mihai", "Elena", "Radu", "Cristian", "Alina",
    "Ionuț", "Bianca", "Sorin", "Teodora", "Ana", "Bogdan", "Irina", "Vlad", "Adriana", "Tudor",
    "Roxana", "Lucian", "Monica", "Sergiu", "Corina", "Matei",
)
INITIALS = "ABCDFGHIMNPRSTV"


def _free_round_starts(db: Session, day, duration_min: int) -> list[datetime]:
    """Grid starts at :00/:30 that do not overlap existing bookings."""
    window = day_window(day)
    if window is None:
        return []
    busy = busy_intervals(db, *window)
    duration = timedelta(minutes=duration_min)
    return [
        s
        for s in grid_starts(day, duration_min)
        if s.minute in (0, 30) and not any(b0 < s + duration and s < b1 for b0, b1 in busy)
    ]


def seed_demo_bookings(db: Session, clock: Clock, *, weeks: int = 2) -> int:
    """Create example bookings for the current and the next week. Returns the count."""
    now = clock.now_local()
    created_at = clock.utc_naive()
    monday = now.date() - timedelta(days=now.weekday())
    rng = random.Random(f"zambet-seed-{monday.isoformat()}")
    phone_suffix = 101
    created = 0

    for week in range(weeks):
        for offset in range(6):  # Monday to Saturday
            day = monday + timedelta(days=7 * week + offset)
            target = rng.randint(4, 6) if offset < 5 else rng.randint(2, 3)
            if week > 0:
                target = max(1, target - 2)  # further ahead looks less busy
            placed = attempts = 0
            while placed < target and attempts < 40:
                attempts += 1
                service = rng.choice(SERVICES)
                free = _free_round_starts(db, day, service.duration_min)
                if not free:
                    continue
                request = NewBooking(
                    service=service,
                    start=rng.choice(free),
                    name=f"{rng.choice(FIRST_NAMES)} {rng.choice(INITIALS)}.",
                    phone=f"0700 000 {phone_suffix:03d}",
                    source="seed",
                )
                try:
                    create_booking(db, request, now=now, created_at=created_at, allow_past=True, rng=rng)
                except BookingError:
                    continue
                phone_suffix += 1
                placed += 1
                created += 1
    return created
