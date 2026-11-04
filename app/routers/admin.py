"""Reception dashboard (/admin): login, week view data, cancellation, live stream."""

from __future__ import annotations

import asyncio
import json
import math
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from .. import scheduling as sch
from ..auth import (
    ADMIN_COOKIE,
    SESSION_HOURS,
    check_password,
    cookie_secure,
    is_admin,
    make_token,
    require_admin,
    require_same_origin,
)
from ..chat.flow import booking_event
from ..chat.texts import error_text
from ..clinic import CLINIC, SERVICES, SERVICES_BY_ID, hours_for
from ..i18n import WEEKDAYS_SHORT, fmt_date, fmt_day_parts, fmt_price, fmt_time, fmt_week_label, plural_bookings_ro
from ..models import Booking
from ..ratelimit import LoginGuard
from ..web import templates
from .chat import client_ip

router = APIRouter()
login_guard = LoginGuard(max_failures=5, lock_seconds=15 * 60)

SOURCE_LABELS = {
    "ai": "Asistent AI, chat pe site",
    "demo": "Mod demo, chat pe site",
    "seed": "Exemplu, regenerat zilnic",
}


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def default_week_start(now: datetime) -> date:
    """This week's Monday; on Sunday (closed) the coming week."""
    today = now.date()
    if today.weekday() == 6:
        return today + timedelta(days=1)
    return today - timedelta(days=today.weekday())


def booking_json(booking: Booking, now: datetime, to_local) -> dict:
    service = SERVICES_BY_ID[booking.service_id]
    created_local = to_local(booking.created_at)
    return {
        "id": booking.id,
        "code": booking.code,
        "service_id": service.id,
        "service_name": service.name["ro"],
        "service_label": service.label["ro"],
        "service_short": service.short["ro"],
        "color": service.color,
        "accent": service.accent,
        "duration_min": service.duration_min,
        "price_label": fmt_price(service.price_ron, "ro"),
        "date": booking.start.date().isoformat(),
        "date_label": fmt_date(booking.start.date(), "ro", year=True),
        "date_short": f"{fmt_date(booking.start.date(), 'ro').split(',')[0]}, {fmt_day_parts(booking.start.date(), 'ro')[1]}",
        "start": fmt_time(booking.start),
        "end": fmt_time(booking.end),
        "start_min": booking.start.hour * 60 + booking.start.minute,
        "end_min": booking.end.hour * 60 + booking.end.minute,
        "patient_name": booking.patient_name,
        "phone": booking.phone,
        "tel": sch.phone_to_tel(booking.phone),
        "status": booking.status,
        "source": booking.source,
        "source_label": SOURCE_LABELS.get(booking.source, booking.source),
        "is_new": booking.source in ("ai", "demo"),
        "created_at": booking.created_at.isoformat(timespec="seconds") + "Z",
        "created_local": created_local.isoformat(timespec="seconds"),
        "is_past": booking.end <= now,
        "can_cancel": booking.status == "confirmed" and booking.end > now,
    }


# --- pages ---------------------------------------------------------------------------------


def _page_context(request: Request, **extra) -> dict:
    settings = request.app.state.settings
    return {
        "clinic": CLINIC,
        "agency_name": settings.agency_name,
        "password_generated": settings.admin_password_generated,
        **extra,
    }


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page(request: Request):
    if not is_admin(request):
        return templates.TemplateResponse(request, "admin/login.html", _page_context(request))
    legend = [{"label": s.label["ro"], "color": s.color, "accent": s.accent} for s in SERVICES]
    return templates.TemplateResponse(request, "admin/dashboard.html", _page_context(request, legend=legend))


@router.post("/admin/login", include_in_schema=False)
def login(request: Request, password: str = Form(default="")):
    require_same_origin(request)
    ip = client_ip(request)
    locked = login_guard.locked_for(ip)
    if locked:
        minutes = math.ceil(locked / 60)
        error = f"Prea multe încercări greșite. Încearcă din nou peste {minutes} {'minut' if minutes == 1 else 'minute'}."
        return templates.TemplateResponse(request, "admin/login.html", _page_context(request, error=error), status_code=429)

    settings = request.app.state.settings
    if not check_password(settings, password[:200]):
        left = login_guard.failure(ip)
        error = (
            f"Parolă greșită. Mai ai {left} {'încercare' if left == 1 else 'încercări'}."
            if left
            else "Parolă greșită. Accesul s-a blocat pentru 15 minute."
        )
        return templates.TemplateResponse(request, "admin/login.html", _page_context(request, error=error), status_code=401)

    login_guard.success(ip)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE,
        make_token(settings, request.app.state.clock.now_utc().timestamp()),
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request),
        path="/",
    )
    return response


@router.post("/admin/logout", include_in_schema=False)
def logout(request: Request):
    require_same_origin(request)
    response = RedirectResponse("/admin", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


# --- API ---------------------------------------------------------------------------------------


@router.get("/api/admin/week", dependencies=[Depends(require_admin)], tags=["admin"])
def week(request: Request, start: str | None = None, db: Session = Depends(get_db)) -> dict:
    clock = request.app.state.clock
    now = clock.now_local()
    this_week = default_week_start(now)
    try:
        monday = sch.parse_date(start) if start else this_week
    except sch.BookingError:
        raise HTTPException(status_code=400, detail="Dată invalidă.") from None
    monday -= timedelta(days=monday.weekday())
    saturday = monday + timedelta(days=5)

    rows = sch.bookings_between(db, datetime.combine(monday, time()), datetime.combine(monday + timedelta(days=7), time()))
    bookings = [booking_json(b, now, clock.utc_naive_to_local) for b in rows]
    active = [b for b in bookings if b["status"] == "confirmed"]

    days = []
    for offset in range(6):
        day = monday + timedelta(days=offset)
        hours = hours_for(day)
        count = sum(1 for b in active if b["date"] == day.isoformat())
        days.append({
            "date": day.isoformat(),
            "weekday": WEEKDAYS_SHORT["ro"][day.weekday()],
            "day": day.day,
            "label": fmt_date(day, "ro"),
            "opens": fmt_time(hours[0]) if hours else None,
            "closes": fmt_time(hours[1]) if hours else None,
            "is_today": day == now.date(),
            "is_past": day < now.date(),
            "count_label": "liber" if count == 0 else plural_bookings_ro(count),
            "count": count,
        })

    return {
        "week_start": monday.isoformat(),
        "label": fmt_week_label(monday, saturday),
        "prev": (monday - timedelta(days=7)).isoformat(),
        "next": (monday + timedelta(days=7)).isoformat(),
        "this_week": this_week.isoformat(),
        "now": now.isoformat(timespec="seconds"),
        "today": now.date().isoformat(),
        "grid": {"start": "09:00", "end": "19:00"},
        "days": days,
        "bookings": bookings,
        "stats": {
            "today": _active_on(db, now.date()),
            "tomorrow": _active_on(db, now.date() + timedelta(days=1)),
            "week": len(active),
        },
    }


def _active_on(db: Session, day: date) -> int:
    rows = sch.bookings_between(db, datetime.combine(day, time()), datetime.combine(day + timedelta(days=1), time()))
    return sum(1 for b in rows if b.status == "confirmed")


@router.post("/api/admin/bookings/{booking_id}/cancel", dependencies=[Depends(require_admin)], tags=["admin"])
def cancel(booking_id: int, request: Request, db: Session = Depends(get_db)):
    require_same_origin(request)
    clock = request.app.state.clock
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Programarea nu există.")
    try:
        sch.cancel_booking(db, booking, by="admin", now=clock.now_local(), cancelled_at=clock.utc_naive())
    except sch.BookingError as exc:
        return JSONResponse({"error": exc.code, "message": error_text(exc, "ro")}, status_code=409)
    request.app.state.bus.publish({"type": "booking.cancelled", "booking": booking_event(booking)})
    return {"booking": booking_json(booking, clock.now_local(), clock.utc_naive_to_local)}


@router.get("/api/admin/stream", tags=["admin"])
async def stream(request: Request):
    """Server-Sent Events: booking.created, booking.cancelled, reset."""
    require_admin(request)
    bus = request.app.state.bus
    queue = bus.subscribe()
    heartbeat = getattr(request.app.state, "sse_heartbeat_seconds", 15)

    async def events():
        try:
            yield "retry: 3000\n\n"
            yield f"event: hello\ndata: {json.dumps({'subscribers': bus.subscriber_count})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
