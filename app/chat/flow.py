"""Booking steps shared by the AI agent and the scripted demo.

A booking is written in two steps, enforced here rather than in the prompt:

1. `propose` validates everything and stores a *pending* proposal in the chat
   session; the visitor sees a summary card.
2. `finalize` turns that exact proposal into a booking, only after an explicit
   confirmation (the button, or an LLM tool call made after the visitor said
   yes to that summary). Nothing else can create a booking from the chat.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..clinic import SERVICES_BY_ID, Service
from ..i18n import fmt_time, weekday_name
from ..models import Booking, ChatMessage
from .. import scheduling as sch
from . import cards
from .context import ChatContext

PENDING_TTL = timedelta(minutes=15)


def booking_event(booking: Booking) -> dict:
    """Payload of the booking.* events sent to the reception dashboard."""
    return {
        "id": booking.id,
        "code": booking.code,
        "service_id": booking.service_id,
        "service_label": SERVICES_BY_ID[booking.service_id].label["ro"],
        "start": booking.start.isoformat(timespec="minutes"),
        "when_label": f"{weekday_name(booking.start.date(), 'ro')}, {fmt_time(booking.start)}",
        "patient_name": booking.patient_name,
        "source": booking.source,
        "status": booking.status,
    }


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def same_phone(a: str, b: str) -> bool:
    try:
        return sch.normalize_phone(a) == sch.normalize_phone(b)
    except sch.BookingError:
        return bool(_digits(a)) and _digits(a) == _digits(b)


def check_limit(ctx: ChatContext) -> None:
    limit = ctx.settings.max_bookings_per_session
    if sch.active_bookings_for_session(ctx.db, ctx.session.id, ctx.now) >= limit:
        raise sch.BookingError("too_many_bookings", n=limit)


def _summary_message(db: Session, pending: dict | None) -> ChatMessage | None:
    if not pending or not pending.get("message_id"):
        return None
    return db.get(ChatMessage, pending["message_id"])


def _set_summary_state(ctx: ChatContext, pending: dict | None, state: str) -> None:
    msg = _summary_message(ctx.db, pending)
    if msg is None or not msg.ui:
        return
    ui = [cards.with_state(c, state) if c.get("type") == "summary" else c for c in msg.ui]
    ctx.update_ui(msg, ui)


def drop_pending(ctx: ChatContext, state: str = "superseded") -> None:
    if ctx.session.pending:
        _set_summary_state(ctx, ctx.session.pending, state)
    ctx.session.pending = None


def propose(ctx: ChatContext, *, service: Service, start: datetime, name: str, phone: str, source: str) -> dict:
    """Validate a booking without writing it. Raises BookingError."""
    sch.validate_slot(service, start, ctx.now)
    clean_name = sch.normalize_name(name)
    clean_phone = sch.normalize_phone(phone)
    end = start + timedelta(minutes=service.duration_min)
    if sch.busy_intervals(ctx.db, start, end):
        raise sch.BookingError("slot_taken")
    check_limit(ctx)

    drop_pending(ctx)
    pending = {
        "id": secrets.token_hex(6),
        "service_id": service.id,
        "start": start.isoformat(timespec="minutes"),
        "name": clean_name,
        "phone": clean_phone,
        "source": source,
        "created_at": ctx.now_utc.isoformat(timespec="seconds"),
        "message_id": None,
    }
    ctx.session.pending = pending
    return pending


def attach_summary(ctx: ChatContext, pending: dict, msg: ChatMessage) -> None:
    ctx.session.pending = {**pending, "message_id": msg.id}


def pending_matches(pending: dict | None, *, service: Service, start: datetime, name: str, phone: str) -> bool:
    if not pending:
        return False
    try:
        clean_name = sch.normalize_name(name)
    except sch.BookingError:
        return False
    return (
        pending["service_id"] == service.id
        and pending["start"] == start.isoformat(timespec="minutes")
        and pending["name"].casefold() == clean_name.casefold()
        and same_phone(pending["phone"], phone)
    )


@dataclass
class Outcome:
    booking: Booking | None = None
    error: str | None = None
    exc: sch.BookingError | None = None
    pending: dict | None = None
    service: Service | None = None
    start: datetime | None = None


def finalize(ctx: ChatContext, *, pending_id: str | None = None) -> Outcome:
    """Create the booking from the session's pending proposal."""
    pending = ctx.session.pending
    if not pending or (pending_id and pending.get("id") != pending_id):
        return Outcome(error="pending_missing")

    service = SERVICES_BY_ID[pending["service_id"]]
    start = datetime.fromisoformat(pending["start"])
    if datetime.fromisoformat(pending["created_at"]) + PENDING_TTL < ctx.now_utc:
        drop_pending(ctx, "expired")
        return Outcome(error="pending_expired", pending=pending, service=service, start=start)

    try:
        check_limit(ctx)
    except sch.BookingError as exc:
        drop_pending(ctx, "expired")
        return Outcome(error=exc.code, exc=exc, pending=pending, service=service, start=start)

    ctx.db.commit()  # persist the chat so far: create_booking may roll back on a race
    request = sch.NewBooking(
        service=service,
        start=start,
        name=pending["name"],
        phone=pending["phone"],
        source=pending.get("source", ctx.mode),
        lang=ctx.lang,
        session_id=ctx.session.id,
    )
    try:
        booking = sch.create_booking(ctx.db, request, now=ctx.now, created_at=ctx.now_utc)
    except sch.BookingError as exc:
        drop_pending(ctx, "expired")
        return Outcome(error=exc.code, exc=exc, pending=pending, service=service, start=start)

    _set_summary_state(ctx, pending, "confirmed")
    ctx.session.pending = None
    confirmed = {**(ctx.demo_state.get("confirmed") or {}), pending["id"]: booking.code}
    ctx.set_demo_state(confirmed=dict(list(confirmed.items())[-5:]))
    ctx.bus.publish({"type": "booking.created", "booking": booking_event(booking)})
    return Outcome(booking=booking, pending=pending, service=service, start=start)


def already_confirmed(ctx: ChatContext, pending_id: str | None) -> str | None:
    """Booking code if this proposal was already confirmed (e.g. a double click)."""
    if not pending_id:
        return None
    return (ctx.demo_state.get("confirmed") or {}).get(pending_id)


def cancel_by_code(ctx: ChatContext, code: str, phone: str | None) -> Booking:
    """Cancel a visitor's own booking: same chat session, or matching phone."""
    booking = sch.find_by_code(ctx.db, code)
    if booking is None or booking.source == "seed":
        raise sch.BookingError("not_found")
    owns = booking.session_id == ctx.session.id
    if not owns and not (phone and same_phone(phone, booking.phone)):
        raise sch.BookingError("not_found")
    sch.cancel_booking(ctx.db, booking, by="patient", now=ctx.now, cancelled_at=ctx.now_utc)
    ctx.bus.publish({"type": "booking.cancelled", "booking": booking_event(booking)})
    return booking


def alternatives(ctx: ChatContext, service: Service, start: datetime, k: int = 3) -> list[datetime]:
    """Free times closest to `start` on the same day."""
    return sch.nearest(sch.available_starts(ctx.db, service, start.date(), ctx.now), start, k)
