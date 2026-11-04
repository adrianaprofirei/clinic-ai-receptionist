"""The four tools the LLM can call, and their server-side execution.

Tools are the only way the model touches data. Every argument is validated
here (service, date, time, opening hours, overlaps, phone, name), and results
never contain other patients' details: availability is returned as free times
only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..clinic import SERVICES, Service, find_service, hours_for
from ..guardrails import mask_phones, resolve_phone
from ..i18n import fmt_date, fmt_time, weekday_name
from .. import scheduling as sch
from . import cards, flow
from .context import ChatContext
from .texts import error_text

log = logging.getLogger("zambet.tools")

SERVICE_IDS = [s.id for s in SERVICES]

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_services",
            "description": "List the clinic's services with duration (minutes) and price (RON).",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": (
                "Free start times for a service on a date (Europe/Bucharest). Omit `date` to get the "
                "earliest day that still has free times. Only offer times returned by this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "enum": SERVICE_IDS, "description": "Service id."},
                    "date": {"type": "string", "description": "YYYY-MM-DD (optional)."},
                },
                "required": ["service"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_booking",
            "description": (
                "Book an appointment in two steps. First call with confirmed=false: validates and shows the "
                "visitor a summary with Confirm/Change buttons (nothing is booked). Call again with the same "
                "details and confirmed=true only after the visitor explicitly confirmed that summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "enum": SERVICE_IDS},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time": {"type": "string", "description": "HH:MM start time, one returned by get_available_slots"},
                    "name": {"type": "string", "description": "Visitor's full name"},
                    "phone": {"type": "string", "description": "Visitor's phone, e.g. [TEL-1]"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["service", "date", "time", "name", "phone", "confirmed"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": "Cancel an existing appointment, identified by its code (ZB-XXXX) and the phone number used.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Booking code, e.g. ZB-4F7K"},
                    "phone": {"type": "string", "description": "Phone used for the booking, e.g. [TEL-1]"},
                },
                "required": ["code", "phone"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class ToolResult:
    data: dict
    ui: list[dict] = field(default_factory=list)
    pending: dict | None = None  # a new proposal whose summary card must be attached


def _hm(value: datetime) -> str:
    return fmt_time(value)


class ToolRunner:
    def __init__(self, ctx: ChatContext) -> None:
        self.ctx = ctx

    @property
    def pii(self) -> dict[str, str]:
        return self.ctx.session.pii or {}

    # --- status lines shown while a tool runs --------------------------------------------

    def status_text(self, name: str, raw_args: str) -> str:
        lang = self.ctx.lang
        try:
            args = json.loads(raw_args or "{}")
        except ValueError:
            args = {}
        if name == "get_available_slots":
            try:
                day = sch.parse_date(args["date"])
            except (KeyError, sch.BookingError):
                return "Caut cea mai apropiată oră liberă…" if lang == "ro" else "Looking for the earliest free time…"
            if lang == "ro":
                return f"Caut orele libere de {self._day_word(day)}…"
            return f"Looking for free times {self._day_word(day)}…"
        if name == "create_booking":
            if args.get("confirmed"):
                return "Fac programarea…" if lang == "ro" else "Booking it…"
            return "Verific detaliile…" if lang == "ro" else "Checking the details…"
        if name == "cancel_booking":
            return "Anulez programarea…" if lang == "ro" else "Cancelling…"
        return "Mă uit în lista de servicii…" if lang == "ro" else "Checking our services…"

    def _day_word(self, day: date) -> str:
        today = self.ctx.now.date()
        if self.ctx.lang == "ro":
            if day == today:
                return "azi"
            if day == today + timedelta(days=1):
                return "mâine"
            return weekday_name(day, "ro")
        if day == today:
            return "today"
        if day == today + timedelta(days=1):
            return "tomorrow"
        return f"on {weekday_name(day, 'en')}"

    # --- execution -----------------------------------------------------------------------------

    def run(self, name: str, raw_args: str) -> ToolResult:
        try:
            args = json.loads(raw_args or "{}")
        except ValueError:
            return ToolResult({"status": "error", "error": "invalid_arguments", "message": "Arguments must be JSON."})
        if not isinstance(args, dict):
            return ToolResult({"status": "error", "error": "invalid_arguments"})
        handler = {
            "list_services": self.list_services,
            "get_available_slots": self.get_available_slots,
            "create_booking": self.create_booking,
            "cancel_booking": self.cancel_booking,
        }.get(name)
        if handler is None:
            return ToolResult({"status": "error", "error": "unknown_tool", "message": f"No tool named {name}."})
        try:
            return handler(args)
        except sch.BookingError as exc:
            return self._error(exc)
        except Exception:  # never leave a tool call without a result
            log.exception("Tool %s failed", name)
            return ToolResult({"status": "error", "error": "internal_error", "message": "Please try again."})

    def _error(self, exc: sch.BookingError, *, service: Service | None = None,
               start: datetime | None = None) -> ToolResult:
        data: dict = {"status": "error", "error": exc.code, "message": error_text(exc, "en")}
        if exc.code == "unknown_service":
            data["valid_services"] = SERVICE_IDS
        ui: list[dict] = []
        if service and start and exc.code in ("slot_taken", "in_past", "too_soon", "outside_hours", "misaligned"):
            alts = flow.alternatives(self.ctx, service, start)
            data["alternatives"] = [_hm(a) for a in alts]
            data["date"] = start.date().isoformat()
            if alts:
                ui.append(cards.slots_card(service, start.date(), alts, self.ctx.lang, "ai"))
        return ToolResult(data, ui)

    def _service(self, args: dict) -> Service:
        service = find_service(str(args.get("service") or ""))
        if service is None:
            raise sch.BookingError("unknown_service", valid=SERVICE_IDS)
        return service

    def list_services(self, _args: dict) -> ToolResult:
        return ToolResult({
            "status": "ok",
            "currency": "RON",
            "services": [
                {"id": s.id, "name_ro": s.name["ro"], "name_en": s.name["en"], "duration_min": s.duration_min,
                 "price_ron": s.price_ron, "description": s.description["en"]}
                for s in SERVICES
            ],
        })

    def get_available_slots(self, args: dict) -> ToolResult:
        ctx = self.ctx
        service = self._service(args)
        base = {"service": service.id, "service_name": service.name["ro"], "duration_min": service.duration_min,
                "price_ron": service.price_ron}

        if args.get("date"):
            day = sch.parse_date(str(args["date"]))
            if day < ctx.now.date():
                raise sch.BookingError("in_past")
            if day > sch.last_bookable_day(ctx.now):
                raise sch.BookingError("too_far", horizon_days=sch.HORIZON_DAYS)
            starts = sch.available_starts(ctx.db, service, day, ctx.now)
            if not starts:
                reason = "closed" if hours_for(day) is None else "fully_booked"
                found = sch.next_available(ctx.db, service, ctx.now, from_day=day + timedelta(days=1))
                data = {**base, "status": reason, "date": day.isoformat(), "weekday": weekday_name(day, "en")}
                ui: list[dict] = []
                if found:
                    next_day, next_starts = found
                    data["next_available"] = {"date": next_day.isoformat(), "weekday": weekday_name(next_day, "en"),
                                              "suggested": [_hm(s) for s in sch.suggest(next_starts, 6)]}
                    ui.append(cards.slots_card(service, next_day, next_starts, ctx.lang, "ai"))
                return ToolResult(data, ui)
        else:
            found = sch.next_available(ctx.db, service, ctx.now)
            if found is None:
                return ToolResult({**base, "status": "none_in_next_30_days"})
            day, starts = found

        opens, closes = hours_for(day)
        data = {
            **base,
            "status": "ok",
            "date": day.isoformat(),
            "weekday": weekday_name(day, "en"),
            "opens": fmt_time(opens),
            "closes": fmt_time(closes),
            "suggested": [_hm(s) for s in sch.suggest(starts, 6)],
            "free_ranges": [[_hm(a), _hm(b)] for a, b in sch.free_ranges(starts)],
            "note": "The visitor sees the suggested times as buttons. Any start inside free_ranges "
                    "(15-minute steps) can also be booked.",
        }
        return ToolResult(data, [cards.slots_card(service, day, starts, ctx.lang, "ai")])

    def create_booking(self, args: dict) -> ToolResult:
        ctx = self.ctx
        service = self._service(args)
        day = sch.parse_date(str(args.get("date") or ""))
        start = datetime.combine(day, sch.parse_time(str(args.get("time") or "")))
        name = str(args.get("name") or "")[:80]
        phone = resolve_phone(args.get("phone"), self.pii)
        confirmed = args.get("confirmed") is True or str(args.get("confirmed")).lower() == "true"

        if not confirmed:
            try:
                pending = flow.propose(ctx, service=service, start=start, name=name, phone=phone, source="ai")
            except sch.BookingError as exc:
                return self._error(exc, service=service, start=start)
            ui = [cards.summary_card(pending, ctx.lang, "ai")]
            end = start + timedelta(minutes=service.duration_min)
            pii = dict(self.pii)
            masked_phone = mask_phones(pending["phone"], pii)
            ctx.session.pii = pii
            return ToolResult(
                {
                    "status": "awaiting_confirmation",
                    "summary": {"service": service.id, "date": day.isoformat(), "start": _hm(start),
                                "end": _hm(end), "name": pending["name"], "phone": masked_phone,
                                "price_ron": service.price_ron},
                    "note": "NOT booked yet. A summary card with Confirm/Change buttons is now visible. Ask the "
                            "visitor to check the details and confirm.",
                },
                ui,
                pending=pending,
            )

        if not flow.pending_matches(ctx.session.pending, service=service, start=start, name=name, phone=phone):
            return ToolResult({
                "status": "error",
                "error": "not_confirmed_by_visitor",
                "message": "Call create_booking with confirmed=false first so the visitor sees the summary, "
                           "then wait for their explicit confirmation.",
            })
        outcome = flow.finalize(ctx)
        if outcome.booking is None:
            if outcome.exc is not None:
                return self._error(outcome.exc, service=service, start=start)
            return ToolResult({"status": "error", "error": outcome.error,
                               "message": "The summary expired. Check availability again."})
        booking = outcome.booking
        card = cards.booking_card(booking, ctx.lang, to_utc=ctx.clock.local_to_utc_naive, stamp_utc=ctx.now_utc)
        return ToolResult(
            {"status": "confirmed", "code": booking.code, "service": service.id,
             "date": booking.start.date().isoformat(), "start": _hm(booking.start), "end": _hm(booking.end)},
            [card],
        )

    def cancel_booking(self, args: dict) -> ToolResult:
        ctx = self.ctx
        phone = resolve_phone(args.get("phone"), self.pii) or None
        booking = flow.cancel_by_code(ctx, str(args.get("code") or ""), phone)
        return ToolResult(
            {"status": "cancelled", "code": booking.code, "service": booking.service_id,
             "date": booking.start.date().isoformat(), "start": _hm(booking.start),
             "day_label": fmt_date(booking.start.date(), "en")},
            [cards.cancelled_card(booking, ctx.lang, "ai")],
        )
