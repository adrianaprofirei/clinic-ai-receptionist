"""Availability and booking rules.

Every check happens here, on the server, whichever channel asks (AI tools,
scripted demo, admin): slots sit on a 15-minute grid, last exactly as long as
the service, fit inside opening hours, never overlap, and are never in the
past. Times are naive clinic-local datetimes.

Double booking is prevented twice: an overlap query gives a friendly error,
and a PRIMARY KEY on 15-minute `slot_claims` makes an overlapping insert fail
inside the database even when two requests race each other.
"""

from __future__ import annotations

import random
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .clinic import SERVICES, Service, hours_for
from .models import Booking, SlotClaim

SLOT_STEP_MIN = 15
LEAD_TIME_MIN = 30  # earliest booking: now + 30 minutes
HORIZON_DAYS = 30  # bookings up to 30 days ahead
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O, 1/I/L

_STEP = timedelta(minutes=SLOT_STEP_MIN)

assert all(s.duration_min % SLOT_STEP_MIN == 0 for s in SERVICES), "durations must fit the slot grid"


class BookingError(Exception):
    """A rule was broken. `code` is stable and mapped to RO/EN messages."""

    def __init__(self, code: str, **details: object) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


# --- parsing ------------------------------------------------------------------

_TIME_RE = re.compile(r"^\s*(\d{1,2})(?:[:.h](\d{2}))?\s*$")


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise BookingError("invalid_date", value=value) from exc


def parse_time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    match = _TIME_RE.match(str(value))
    if not match:
        raise BookingError("invalid_time", value=value)
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        raise BookingError("invalid_time", value=value)
    return time(hour, minute)


# --- the slot grid --------------------------------------------------------------


def earliest_start(now: datetime) -> datetime:
    """First bookable moment: now + lead time, rounded up to the grid."""
    t = now + timedelta(minutes=LEAD_TIME_MIN)
    floored = t.replace(minute=t.minute - t.minute % SLOT_STEP_MIN, second=0, microsecond=0)
    return floored if floored == t else floored + _STEP


def last_bookable_day(now: datetime) -> date:
    return now.date() + timedelta(days=HORIZON_DAYS)


def day_window(day: date) -> tuple[datetime, datetime] | None:
    hours = hours_for(day)
    if hours is None:
        return None
    return datetime.combine(day, hours[0]), datetime.combine(day, hours[1])


def grid_starts(day: date, duration_min: int) -> list[datetime]:
    """All start times on the grid where the service fits inside opening hours."""
    window = day_window(day)
    if window is None:
        return []
    opens, closes = window
    duration = timedelta(minutes=duration_min)
    starts: list[datetime] = []
    t = opens
    while t + duration <= closes:
        starts.append(t)
        t += _STEP
    return starts


def busy_intervals(db: Session, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Active bookings overlapping [start, end)."""
    rows = db.execute(
        select(Booking.start, Booking.end).where(
            Booking.status == "confirmed", Booking.start < end, Booking.end > start
        )
    ).all()
    return [(row.start, row.end) for row in rows]


def _overlaps(start: datetime, end: datetime, intervals: list[tuple[datetime, datetime]]) -> bool:
    return any(s < end and start < e for s, e in intervals)


def available_starts(db: Session, service: Service, day: date, now: datetime) -> list[datetime]:
    """Free start times for `service` on `day`, as of `now`."""
    if day < now.date() or day > last_bookable_day(now):
        return []
    starts = grid_starts(day, service.duration_min)
    if not starts:
        return []
    lower = earliest_start(now)
    duration = timedelta(minutes=service.duration_min)
    busy = busy_intervals(db, starts[0], starts[-1] + duration)
    return [s for s in starts if s >= lower and not _overlaps(s, s + duration, busy)]


def next_available(
    db: Session, service: Service, now: datetime, *, from_day: date | None = None
) -> tuple[date, list[datetime]] | None:
    """The first day (from `from_day`, default today) with at least one free slot."""
    day = max(from_day or now.date(), now.date())
    last = last_bookable_day(now)
    while day <= last:
        starts = available_starts(db, service, day, now)
        if starts:
            return day, starts
        day += timedelta(days=1)
    return None


def bookable_days(db: Session, service: Service, now: datetime, count: int) -> list[date]:
    """The next `count` days that still have room for `service`."""
    days: list[date] = []
    day = now.date()
    last = last_bookable_day(now)
    while day <= last and len(days) < count:
        if available_starts(db, service, day, now):
            days.append(day)
        day += timedelta(days=1)
    return days


def suggest(starts: list[datetime], k: int = 6) -> list[datetime]:
    """Pick up to k well-spread times, preferring :00 and :30."""
    if len(starts) <= k:
        return list(starts)
    preferred = [s for s in starts if s.minute in (0, 30)]
    pool = preferred if len(preferred) >= k else starts
    step = (len(pool) - 1) / (k - 1)
    picked = {pool[round(i * step)] for i in range(k)}
    return sorted(picked)


def free_ranges(starts: list[datetime]) -> list[tuple[datetime, datetime]]:
    """Group consecutive grid starts: [(first_start, last_start), ...]."""
    ranges: list[tuple[datetime, datetime]] = []
    for s in starts:
        if ranges and s - ranges[-1][1] == _STEP:
            ranges[-1] = (ranges[-1][0], s)
        else:
            ranges.append((s, s))
    return ranges


def nearest(starts: list[datetime], target: datetime, k: int = 3) -> list[datetime]:
    """The k free times closest to `target`, in chronological order."""
    closest = sorted(starts, key=lambda s: (abs((s - target).total_seconds()), s))[:k]
    return sorted(closest)


# --- validation -------------------------------------------------------------------


def validate_slot(service: Service, start: datetime, now: datetime, *, allow_past: bool = False) -> datetime:
    """Raise BookingError if `service` cannot start at `start`; return the end."""
    if start.second or start.microsecond or start.minute % SLOT_STEP_MIN:
        raise BookingError("misaligned", step=SLOT_STEP_MIN)
    window = day_window(start.date())
    if window is None:
        raise BookingError("closed_day", day=start.date())
    opens, closes = window
    end = start + timedelta(minutes=service.duration_min)
    if start < opens or end > closes:
        latest = closes - timedelta(minutes=service.duration_min)
        raise BookingError("outside_hours", opens=opens.time(), closes=closes.time(), latest=latest.time())
    if not allow_past:
        if start < now:
            raise BookingError("in_past")
        if start < earliest_start(now):
            raise BookingError("too_soon", lead_minutes=LEAD_TIME_MIN)
        if start.date() > last_bookable_day(now):
            raise BookingError("too_far", horizon_days=HORIZON_DAYS)
    return end


_PHONE_SEPARATORS = re.compile(r"[\s\-./()]")
_RO_PHONE = re.compile(r"0[237]\d{8}")
_INTL_PHONE = re.compile(r"\+[1-9]\d{7,14}")
_NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .'\-’])*$")


def normalize_phone(raw: str) -> str:
    """'+40 712-345-678' -> '0712 345 678'. Foreign numbers stay in E.164."""
    digits = _PHONE_SEPARATORS.sub("", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+40"):
        digits = "0" + digits[3:]
    elif digits.startswith("40") and len(digits) == 11:
        digits = "0" + digits[2:]
    if _RO_PHONE.fullmatch(digits):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    if _INTL_PHONE.fullmatch(digits):
        return digits
    raise BookingError("invalid_phone")


def phone_to_tel(phone: str) -> str:
    """Display phone -> value for a tel: link."""
    compact = phone.replace(" ", "")
    return "+4" + compact if compact.startswith("0") else compact


def normalize_name(raw: str) -> str:
    name = " ".join((raw or "").split())
    if not 2 <= len(name) <= 60 or not _NAME_RE.match(name) or sum(c.isalpha() for c in name) < 2:
        raise BookingError("invalid_name")
    if name == name.lower() or name == name.upper():
        name = name.title()
    return name


def normalize_code(raw: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if compact.startswith("ZB"):
        compact = compact[2:]
    return f"ZB-{compact}"


# --- writes ------------------------------------------------------------------------


def _blocks(start: datetime, end: datetime) -> list[datetime]:
    blocks, t = [], start
    while t < end:
        blocks.append(t)
        t += _STEP
    return blocks


def _new_code(db: Session, rng: random.Random | None) -> str:
    choice = rng.choice if rng else secrets.choice
    while True:
        code = "ZB-" + "".join(choice(CODE_ALPHABET) for _ in range(4))
        if db.scalar(select(func.count()).where(Booking.code == code)) == 0:
            return code


@dataclass(frozen=True)
class NewBooking:
    service: Service
    start: datetime
    name: str
    phone: str
    source: str  # ai | demo | seed
    lang: str = "ro"
    session_id: str | None = None


def create_booking(
    db: Session,
    req: NewBooking,
    *,
    now: datetime,
    created_at: datetime,
    allow_past: bool = False,
    rng: random.Random | None = None,
) -> Booking:
    """Validate and insert a booking, or raise BookingError."""
    end = validate_slot(req.service, req.start, now, allow_past=allow_past)
    name = normalize_name(req.name)
    phone = normalize_phone(req.phone)

    if busy_intervals(db, req.start, end):
        raise BookingError("slot_taken")

    for _attempt in range(3):
        booking = Booking(
            code=_new_code(db, rng),
            service_id=req.service.id,
            start=req.start,
            end=end,
            patient_name=name,
            phone=phone,
            status="confirmed",
            source=req.source,
            lang=req.lang,
            session_id=req.session_id,
            created_at=created_at,
        )
        booking.claims = [SlotClaim(block_start=b) for b in _blocks(req.start, end)]
        db.add(booking)
        try:
            db.commit()
            return booking
        except IntegrityError:
            db.rollback()
            # Lost a race: either the slot (claims) or, very rarely, the code.
            if busy_intervals(db, req.start, end):
                raise BookingError("slot_taken") from None
    raise BookingError("slot_taken")


def cancel_booking(db: Session, booking: Booking, *, by: str, now: datetime, cancelled_at: datetime) -> Booking:
    """Cancel an active, not yet finished booking and free its slot."""
    if booking.status == "cancelled":
        raise BookingError("already_cancelled")
    if booking.end <= now:
        raise BookingError("already_finished")
    booking.status = "cancelled"
    booking.cancelled_at = cancelled_at
    booking.cancelled_by = by
    booking.claims.clear()
    db.commit()
    return booking


# --- reads -------------------------------------------------------------------------


def find_by_code(db: Session, code: str) -> Booking | None:
    return db.scalar(select(Booking).where(Booking.code == normalize_code(code)))


def bookings_between(db: Session, start: datetime, end: datetime) -> list[Booking]:
    return list(
        db.scalars(select(Booking).where(Booking.start >= start, Booking.start < end).order_by(Booking.start))
    )


def active_bookings_for_session(db: Session, session_id: str, now: datetime) -> int:
    return db.scalar(
        select(func.count()).where(
            Booking.session_id == session_id, Booking.status == "confirmed", Booking.end > now
        )
    ) or 0
