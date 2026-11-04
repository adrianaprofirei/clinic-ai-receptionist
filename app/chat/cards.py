"""UI cards attached to assistant messages.

Cards are built on the server from real data (never from LLM text), so a slot
button or a booking confirmation always reflects the database. Each button
carries a `send` payload: `{"text": ...}` posts a chat message (AI mode),
`{"action": {...}, "echo": ...}` triggers a scripted step (demo mode, confirm).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..clinic import CLINIC, SERVICES, SERVICES_BY_ID, Service, hours_for
from ..i18n import fmt_date, fmt_day_parts, fmt_price, fmt_range, fmt_time, norm_lang, weekday_name
from .. import scheduling as sch
from .texts import t

PRIVACY_URL = "/confidentialitate"


# --- send payloads ------------------------------------------------------------------


def send_text(text: str) -> dict:
    return {"text": text}


def send_action(kind: str, echo: str | None = None, **data: object) -> dict:
    payload: dict = {"action": {"type": kind, **data}}
    if echo:
        payload["echo"] = echo
    return payload


def option(label: str, send: dict, tone: str | None = None, **extra: object) -> dict:
    item = {"label": label, "send": send, **extra}
    if tone:
        item["tone"] = tone
    return item


# --- menus ------------------------------------------------------------------------------


def quick_replies(options: list[dict]) -> dict:
    return {"type": "quick_replies", "options": options}


def main_menu(lang: str, mode: str) -> dict:
    items = [("qr_book", "book", None), ("qr_prices", "prices", None), ("qr_hours", "hours", None),
             ("qr_pain", "pain", "danger")]
    options = []
    for key, value, tone in items:
        label = t(key, lang)
        send = send_text(label) if mode == "ai" else send_action("menu", label, value=value)
        options.append(option(label, send, tone))
    return quick_replies(options)


def menu_reply(lang: str, mode: str, keys: list[tuple[str, str]]) -> dict:
    """Quick replies from (text_key, menu_value) pairs."""
    options = []
    for key, value in keys:
        label = t(key, lang)
        send = send_text(label) if mode == "ai" else send_action("menu", label, value=value)
        options.append(option(label, send))
    return quick_replies(options)


def services_card(lang: str) -> dict:
    return {
        "type": "services",
        "label": t("ask_service", lang),
        "options": [
            option(
                s.t("name", lang),
                send_action("service", s.t("name", lang), value=s.id),
                meta=f"{s.duration_min} min · {fmt_price(s.price_ron, lang)}",
            )
            for s in SERVICES
        ],
    }


def price_list_card(lang: str) -> dict:
    return {
        "type": "price_list",
        "rows": [
            {"label": s.t("name", lang), "meta": f"{s.duration_min} min", "value": fmt_price(s.price_ron, lang)}
            for s in SERVICES
        ],
        "note": t("prices_note", lang),
    }


# --- days and slots --------------------------------------------------------------------


def days_card(days: list[date], lang: str) -> dict:
    options = []
    for d in days:
        top, bottom = fmt_day_parts(d, lang)
        label = fmt_date(d, lang)
        options.append(option(label, send_action("day", label, value=d.isoformat()), top=top, bottom=bottom))
    return {"type": "days", "label": t("days_label", lang), "options": options}


def slots_card(service: Service, day: date, starts: list[datetime], lang: str, mode: str) -> dict:
    """AI mode: a boxed card with ~6 suggestions; demo mode: a 4-column grid."""
    limit = 6 if mode == "ai" else 8
    shown = sch.suggest(starts, limit) if len(starts) > limit else list(starts)
    more = [s for s in starts if s not in shown]

    def slot_option(start: datetime) -> dict:
        label = fmt_time(start)
        if mode == "ai":
            return option(label, send_text(label), value=start.isoformat(timespec="minutes"))
        return option(label, send_action("slot", label, value=start.isoformat(timespec="minutes")),
                      value=start.isoformat(timespec="minutes"))

    if mode == "ai":
        other = option(t("other_day", lang), send_text(t("other_day", lang)))
    else:
        other = option(t("other_day", lang), send_action("other_day", t("other_day", lang)))

    return {
        "type": "slots",
        "layout": "card" if mode == "ai" else "grid",
        "date": day.isoformat(),
        "date_label": fmt_date(day, lang),
        "meta": f"{service.t('label', lang)} · {service.duration_min} min",
        "group_label": t("slots_label", lang, day=fmt_date(day, lang, capital=False)),
        "service_id": service.id,
        "options": [slot_option(s) for s in shown],
        "more": [slot_option(s) for s in more],
        "more_label": t("more_times", lang),
        "other_day": other,
        "selected": None,
    }


# --- personal data -----------------------------------------------------------------------


def consent_card(lang: str) -> dict:
    return {
        "type": "consent",
        "text": t("consent", lang),
        "link": {"label": t("consent_link", lang), "href": PRIVACY_URL},
    }


def details_form_card(
    lang: str, *, values: dict | None = None, errors: dict | None = None, state: str = "open"
) -> dict:
    return {
        "type": "details_form",
        "state": state,
        "consent": t("consent_form", lang),
        "link": {"label": t("consent_link", lang), "href": PRIVACY_URL},
        "fields": [
            {"name": "name", "label": t("name_label", lang), "placeholder": t("name_placeholder", lang),
             "autocomplete": "name", "type": "text"},
            {"name": "phone", "label": t("phone_label", lang), "placeholder": t("phone_placeholder", lang),
             "autocomplete": "tel", "type": "tel"},
        ],
        "values": values or {"name": "", "phone": ""},
        "errors": errors or {},
        "submit": t("continue", lang),
        "done_label": t("details_sent", lang),
    }


# --- summary and confirmation --------------------------------------------------------------


def _when(start: datetime, end: datetime, lang: str) -> str:
    return f"{fmt_date(start.date(), lang)}, {fmt_range(start, end)}"


def summary_card(pending: dict, lang: str, mode: str, state: str = "pending") -> dict:
    service = SERVICES_BY_ID[pending["service_id"]]
    start = datetime.fromisoformat(pending["start"])
    end = start + timedelta(minutes=service.duration_min)
    label = service.t("label", lang)
    confirm_label, modify_label = t("confirm", lang), t("modify", lang)
    modify_send = send_text(modify_label) if mode == "ai" else send_action("modify", modify_label)
    return {
        "type": "summary",
        "pending_id": pending["id"],
        "state": state,
        "title": t("summary_title", lang),
        "rows": [
            {"label": t("row_service", lang), "value": f"{label} · {service.duration_min} min"},
            {"label": t("row_when", lang), "value": _when(start, end, lang), "numeric": True},
            {"label": t("row_name", lang), "value": pending["name"]},
            {"label": t("row_phone", lang), "value": pending["phone"], "numeric": True},
            {"label": t("row_price", lang),
             "value": t("price_at_clinic", lang, price=fmt_price(service.price_ron, lang))},
        ],
        "compact": " · ".join([label, _when(start, end, lang), pending["name"], pending["phone"]]),
        "confirm": option(confirm_label, send_action("confirm", confirm_label, pending_id=pending["id"])),
        "modify": option(modify_label, modify_send),
        "state_labels": {
            "confirmed": t("confirmed_label", lang),
            "superseded": t("superseded_label", lang),
            "expired": t("expired_label", lang),
        },
    }


def with_state(card: dict, state: str) -> dict:
    return {**card, "state": state}


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    """Fold content lines at 75 octets (RFC 5545)."""
    out, current = [], ""
    for ch in line:
        if len((current + ch).encode()) > 75:
            out.append(current)
            current = " " + ch
        else:
            current += ch
    out.append(current)
    return "\r\n".join(out)


def ics_event(*, uid: str, start_utc: datetime, end_utc: datetime, stamp_utc: datetime,
              title: str, location: str, description: str) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Clinica Zambet demo//RO",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp_utc.strftime(fmt)}",
        f"DTSTART:{start_utc.strftime(fmt)}",
        f"DTEND:{end_utc.strftime(fmt)}",
        f"SUMMARY:{_ics_escape(title)}",
        f"LOCATION:{_ics_escape(location)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


def booking_card(booking, lang: str, *, to_utc, stamp_utc: datetime) -> dict:
    service = SERVICES_BY_ID[booking.service_id]
    title = t("calendar_title", lang, service=service.t("label", lang), clinic=CLINIC.name)
    return {
        "type": "booking",
        "title": t("booked_title", lang),
        "when": [fmt_date(booking.start.date(), lang), fmt_range(booking.start, booking.end, " – ")],
        "rows": [
            {"label": t("row_service", lang),
             "value": f"{service.t('label', lang)} · {fmt_price(service.price_ron, lang)}"},
            {"label": t("row_for", lang), "value": booking.patient_name},
            {"label": t("row_address", lang), "value": CLINIC.address},
            {"label": t("row_code", lang), "value": booking.code, "code": True},
        ],
        "code": booking.code,
        "calendar": {
            "label": t("add_calendar", lang),
            "filename": f"programare-{booking.code}.ics",
            "ics": ics_event(
                uid=f"{booking.code}@zambet.demo",
                start_utc=to_utc(booking.start),
                end_utc=to_utc(booking.end),
                stamp_utc=stamp_utc,
                title=title,
                location=CLINIC.address,
                description=t("calendar_description", lang, code=booking.code, phone=CLINIC.phone_display),
            ),
        },
    }


def cancelled_card(booking, lang: str, mode: str) -> dict:
    label = t("pick_another", lang)
    send = send_text(t("pick_another_text", lang)) if mode == "ai" else send_action("menu", label, value="book")
    return {
        "type": "cancelled",
        "title": t("cancelled_title", lang),
        "text": t(
            "cancelled_text",
            lang,
            date=fmt_date(booking.start.date(), lang, capital=False),
            time=fmt_time(booking.start),
            code=booking.code,
        ),
        "action": option(label, send),
    }


# --- safety and notices ------------------------------------------------------------------


def next_opening(now: datetime) -> tuple[date, time]:
    day = now.date()
    for _ in range(8):
        hours = hours_for(day)
        if hours and (day > now.date() or now.time() < hours[0]):
            return day, hours[0]
        day += timedelta(days=1)
    raise RuntimeError("clinic never opens")  # pragma: no cover


def _relative_day(day: date, now: datetime, lang: str) -> str:
    lang = norm_lang(lang)
    if day == now.date():
        return "azi" if lang == "ro" else "today"
    if day == now.date() + timedelta(days=1):
        return "mâine" if lang == "ro" else "tomorrow"
    return weekday_name(day, lang) if lang == "ro" else f"on {weekday_name(day, lang)}"


def emergency_card(lang: str, now: datetime) -> dict:
    hours = hours_for(now.date())
    if hours and hours[0] <= now.time() < hours[1]:
        line = t("emergency_open", lang, closes=fmt_time(hours[1]))
        if now.weekday() == 5:
            line = line.split(" · ")[0]
    else:
        day, opens = next_opening(now)
        at = "la" if norm_lang(lang) == "ro" else "at"
        line = t("emergency_closed", lang, next=f"{_relative_day(day, now, lang)} {at} {fmt_time(opens)}")
    return {
        "type": "emergency",
        "call": {"label": t("emergency_call", lang, phone=CLINIC.phone_display), "tel": CLINIC.phone_tel},
        "hours": line,
        "warning": t("emergency_112", lang),
    }


def notice(text: str, *, tone: str = "info", icon: str = "info", action: dict | None = None,
           link: dict | None = None) -> dict:
    card: dict = {"type": "notice", "tone": tone, "icon": icon, "text": text}
    if action:
        card["action"] = action
    if link:
        card["link"] = link
    return card
