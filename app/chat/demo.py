"""DEMO MODE: a scripted, button-driven booking flow.

Used when OPENAI_API_KEY is missing, when the daily AI budget is spent, or
when the AI provider fails, so the demo always works:

    service -> day -> free slot -> name/phone (with consent) -> summary -> confirm

Every step re-checks availability, so stale buttons are handled gracefully.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..clinic import CLINIC, SERVICES_BY_ID, Service, hours_for
from ..i18n import fmt_date, fmt_time, weekday_name
from .. import scheduling as sch
from . import cards, flow
from .context import ChatContext
from .texts import error_text, phrase, t

# --- entry points ------------------------------------------------------------------------


def greet(ctx: ChatContext) -> None:
    if ctx.mode == "ai":
        ctx.say(t("greeting", "ro"), lang="ro")
        ctx.say("You can also write to me in English.", ui=[cards.main_menu("ro", "ai")], lang="en")
    else:
        ctx.say(t("greeting_demo", ctx.lang), ui=[cards.main_menu(ctx.lang, "demo")], in_llm=False)


def handle_text(ctx: ChatContext, text: str) -> None:
    """Free text is disabled in demo mode; answer kindly with the menu."""
    ctx.user(text, in_llm=False)
    ctx.say(t("demo_text_disabled", ctx.lang), ui=[cards.main_menu(ctx.lang, "demo")], in_llm=False)


def handle_action(ctx: ChatContext, action: dict) -> None:
    handler = _HANDLERS.get(str(action.get("type")))
    if handler is None:
        _fallback(ctx)
        return
    handler(ctx, action)


# --- helpers --------------------------------------------------------------------------------


def _echo(ctx: ChatContext, text: str) -> None:
    ctx.user(text, in_llm=ctx.mode == "ai")


def _say(ctx: ChatContext, text: str | None, ui: list[dict] | None = None, **kwargs) -> None:
    kwargs.setdefault("in_llm", ctx.mode == "ai")
    ctx.say(text, ui, **kwargs)


def _menu(ctx: ChatContext) -> dict:
    return cards.main_menu(ctx.lang, ctx.mode)


def _state_service(ctx: ChatContext) -> Service | None:
    return SERVICES_BY_ID.get(ctx.demo_state.get("service") or "")


def _parse_dt(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _weekday(day: date, ctx: ChatContext) -> str:
    """'joi' / 'Thursday', or 'azi' / 'today'."""
    if day == ctx.now.date():
        return "azi" if ctx.lang == "ro" else "today"
    return weekday_name(day, ctx.lang)


# --- steps ------------------------------------------------------------------------------------


def ask_service(ctx: ChatContext) -> None:
    flow.drop_pending(ctx)
    ctx.set_demo_state(step=1, service=None, day=None, slot=None)
    _say(ctx, t("ask_service", ctx.lang), [cards.services_card(ctx.lang)])


def ask_day(ctx: ChatContext, service: Service) -> None:
    days = sch.bookable_days(ctx.db, service, ctx.now, 8)
    if not days:
        _say(ctx, t("no_days", ctx.lang, phrase=phrase(service.id, ctx.lang), phone=CLINIC.phone_display),
             [_menu(ctx)])
        return
    ctx.set_demo_state(step=2, service=service.id, day=None, slot=None)
    _say(ctx, t("ask_day", ctx.lang, phrase=phrase(service.id, ctx.lang)), [cards.days_card(days, ctx.lang)])


def show_slots(ctx: ChatContext, service: Service, day: date, *, intro: str | None = None) -> None:
    starts = sch.available_starts(ctx.db, service, day, ctx.now)
    if not starts:
        days = sch.bookable_days(ctx.db, service, ctx.now, 8)
        ctx.set_demo_state(step=2, service=service.id, day=None)
        _say(ctx, t("day_full", ctx.lang, phrase=phrase(service.id, ctx.lang)),
             [cards.days_card(days, ctx.lang)] if days else [_menu(ctx)])
        return
    if intro is None:
        opens, closes = hours_for(day)
        values = {"phrase": phrase(service.id, ctx.lang), "duration": service.duration_min,
                  "opens": fmt_time(opens), "closes": fmt_time(closes)}
        if day == ctx.now.date():
            intro = t("slots_intro_today", ctx.lang, **values)
        else:
            wd = weekday_name(day, ctx.lang)
            intro = t("slots_intro", ctx.lang, day=wd[:1].upper() + wd[1:], **values)
    ctx.set_demo_state(step=3, service=service.id, day=day.isoformat(), slot=None)
    listed = ", ".join(fmt_time(s) for s in sch.suggest(starts, 8))
    _say(ctx, intro, [cards.slots_card(service, day, starts, ctx.lang, ctx.mode)],
         llm_content=f"{intro} {listed} ({day.isoformat()})")


def ask_details(ctx: ChatContext, *, values: dict | None = None, errors: dict | None = None) -> None:
    ctx.session.consent_shown = True
    ctx.set_demo_state(step=4)
    _say(ctx, None, [cards.details_form_card(ctx.lang, values=values, errors=errors)], in_llm=False)


def propose(ctx: ChatContext, service: Service, start: datetime, name: str, phone: str) -> None:
    try:
        pending = flow.propose(ctx, service=service, start=start, name=name, phone=phone, source="demo")
    except sch.BookingError as exc:
        if exc.code in ("invalid_name", "invalid_phone"):
            ask_details(ctx, values={"name": name, "phone": phone},
                        errors={exc.code.removeprefix("invalid_"): error_text(exc, ctx.lang)})
        elif exc.code == "too_many_bookings":
            _say(ctx, t("too_many_bookings", ctx.lang, n=exc.details["n"], phone=CLINIC.phone_display))
        else:
            slot_problem(ctx, service, start, exc)
        return
    first = pending["name"].split()[0]
    msg = ctx.say(t("summary_intro", ctx.lang, first=first), [cards.summary_card(pending, ctx.lang, "demo")],
                  in_llm=False)
    flow.attach_summary(ctx, pending, msg)


def slot_problem(ctx: ChatContext, service: Service, start: datetime, exc: sch.BookingError) -> None:
    """The chosen time cannot be booked: offer the nearest free times that day."""
    alts = flow.alternatives(ctx, service, start)
    day_word = _weekday(start.date(), ctx)
    if exc.code == "slot_taken":
        text = t("slot_taken", ctx.lang, time=fmt_time(start), day=day_word)
    else:
        text = error_text(exc, ctx.lang)
    if alts:
        ctx.set_demo_state(step=3, service=service.id, day=start.date().isoformat())
        listed = ", ".join(fmt_time(s) for s in alts)
        _say(ctx, text, [cards.slots_card(service, start.date(), alts, ctx.lang, ctx.mode)],
             llm_content=f"{text} {listed} ({start.date().isoformat()})")
        return
    days = sch.bookable_days(ctx.db, service, ctx.now, 8)
    ctx.set_demo_state(step=2, service=service.id, day=None)
    if exc.code == "slot_taken":
        text = t("slot_taken_none", ctx.lang, time=fmt_time(start), day=day_word)
    _say(ctx, text, [cards.days_card(days, ctx.lang)] if days else [_menu(ctx)])


def confirm(ctx: ChatContext, action: dict) -> None:
    """Explicit confirmation (button). Shared by both modes; never uses the LLM."""
    lang = ctx.lang
    code = flow.already_confirmed(ctx, action.get("pending_id"))
    if code:
        _say(ctx, t("already_confirmed", lang, code=code))
        return
    _echo(ctx, t("confirm", lang))
    outcome = flow.finalize(ctx, pending_id=action.get("pending_id"))
    if outcome.booking:
        booking = outcome.booking
        service = SERVICES_BY_ID[booking.service_id]
        card = cards.booking_card(booking, lang, to_utc=ctx.clock.local_to_utc_naive, stamp_utc=ctx.now_utc)
        ctx.say(None, [card], in_llm=False)
        follow = t("booked_followup_ai" if ctx.mode == "ai" else "booked_followup_demo", lang,
                   phone=CLINIC.phone_display)
        note = t("booked_note_llm", lang, code=booking.code, service=service.t("label", lang),
                 when=f"{fmt_date(booking.start.date(), lang)} {fmt_time(booking.start)}", name=booking.patient_name)
        menu = cards.menu_reply(lang, ctx.mode, [("qr_directions", "directions"), ("qr_thanks", "thanks")])
        _say(ctx, follow, [menu], llm_content=f"{note}\n{follow}")
        ctx.set_demo_state(step=0, service=None, day=None, slot=None)
        return

    if outcome.error == "pending_missing":
        _say(ctx, t("pending_missing", lang), [_menu(ctx)])
    elif outcome.error == "pending_expired":
        _say(ctx, t("pending_expired", lang))
        show_slots(ctx, outcome.service, outcome.start.date())
    elif outcome.error == "too_many_bookings":
        _say(ctx, t("too_many_bookings", lang, n=outcome.exc.details["n"], phone=CLINIC.phone_display))
    else:
        slot_problem(ctx, outcome.service, outcome.start, outcome.exc)


# --- action handlers ----------------------------------------------------------------------------


def _fallback(ctx: ChatContext) -> None:
    _say(ctx, t("fallback_reply", ctx.lang), [_menu(ctx)])


_MENU_LABELS = {
    "book": "qr_book", "new_booking": "qr_new_booking", "prices": "qr_prices", "hours": "qr_hours",
    "pain": "qr_pain", "directions": "qr_directions", "thanks": "qr_thanks", "earliest": "qr_earliest",
    "no_thanks": "qr_no_thanks",
}


def _on_menu(ctx: ChatContext, action: dict) -> None:
    value = str(action.get("value"))
    if value not in _MENU_LABELS:
        _fallback(ctx)
        return
    _echo(ctx, t(_MENU_LABELS[value], ctx.lang))
    lang = ctx.lang
    if value in ("book", "new_booking"):
        ask_service(ctx)
    elif value == "prices":
        _say(ctx, t("prices_intro", lang), [cards.price_list_card(lang),
             cards.menu_reply(lang, ctx.mode, [("qr_book", "book"), ("qr_hours", "hours")])])
    elif value == "hours":
        _say(ctx, t("hours_reply", lang, address=CLINIC.address, phone=CLINIC.phone_display),
             [cards.menu_reply(lang, ctx.mode, [("qr_book", "book"), ("qr_prices", "prices")])])
    elif value == "pain":
        _say(ctx, t("pain_reply", lang), [cards.emergency_card(lang, ctx.now)])
        _say(ctx, t("pain_offer", lang),
             [cards.menu_reply(lang, ctx.mode, [("qr_earliest", "earliest"), ("qr_no_thanks", "no_thanks")])])
    elif value == "earliest":
        service = SERVICES_BY_ID["consultatie"]
        found = sch.next_available(ctx.db, service, ctx.now)
        if found is None:
            _say(ctx, t("no_days", lang, phrase=phrase(service.id, lang), phone=CLINIC.phone_display))
            return
        day, _starts = found
        show_slots(ctx, service, day, intro=t("earliest_intro", lang, day=fmt_date(day, lang, capital=False)))
    elif value == "directions":
        _say(ctx, t("directions", lang, address=CLINIC.address))
    elif value == "thanks":
        _say(ctx, t("thanks_reply", lang), [cards.menu_reply(lang, ctx.mode, [("qr_new_booking", "new_booking")])])
    elif value == "no_thanks":
        _say(ctx, t("no_thanks_reply", lang), [_menu(ctx)])


def _on_service(ctx: ChatContext, action: dict) -> None:
    service = SERVICES_BY_ID.get(str(action.get("value")))
    if service is None:
        _fallback(ctx)
        return
    _echo(ctx, service.t("name", ctx.lang))
    flow.drop_pending(ctx)
    ask_day(ctx, service)


def _on_day(ctx: ChatContext, action: dict) -> None:
    service = _state_service(ctx)
    try:
        day = sch.parse_date(str(action.get("value")))
    except sch.BookingError:
        day = None
    if service is None or day is None:
        ask_service(ctx)
        return
    _echo(ctx, fmt_date(day, ctx.lang))
    show_slots(ctx, service, day)


def _on_other_day(ctx: ChatContext, action: dict) -> None:
    service = _state_service(ctx)
    _echo(ctx, t("other_day", ctx.lang))
    if service is None:
        ask_service(ctx)
    else:
        ask_day(ctx, service)


def _on_slot(ctx: ChatContext, action: dict) -> None:
    service = _state_service(ctx)
    start = _parse_dt(action.get("value"))
    if service is None or start is None:
        ask_service(ctx)
        return
    _echo(ctx, fmt_time(start))
    try:
        sch.validate_slot(service, start, ctx.now)
        if sch.busy_intervals(ctx.db, start, start + timedelta(minutes=service.duration_min)):
            raise sch.BookingError("slot_taken")
    except sch.BookingError as exc:
        slot_problem(ctx, service, start, exc)
        return
    state = ctx.set_demo_state(step=4, slot=start.isoformat(timespec="minutes"))
    if state.get("name") and state.get("phone"):
        propose(ctx, service, start, state["name"], state["phone"])
    else:
        ask_details(ctx)


def _form_message(ctx: ChatContext, message_id: object):
    from ..models import ChatMessage

    try:
        msg = ctx.db.get(ChatMessage, int(message_id))
    except (TypeError, ValueError):
        return None
    if msg is None or msg.session_id != ctx.session.id or not msg.ui:
        return None
    return msg if any(c.get("type") == "details_form" for c in msg.ui) else None


def _on_details(ctx: ChatContext, action: dict) -> None:
    name = str(action.get("name") or "")[:80]
    phone = str(action.get("phone") or "")[:32]
    errors: dict[str, str] = {}
    try:
        name = sch.normalize_name(name)
    except sch.BookingError as exc:
        errors["name"] = error_text(exc, ctx.lang)
    try:
        phone = sch.normalize_phone(phone)
    except sch.BookingError as exc:
        errors["phone"] = error_text(exc, ctx.lang)

    form_msg = _form_message(ctx, action.get("message_id"))
    values = {"name": name, "phone": phone}
    if errors:
        card = cards.details_form_card(ctx.lang, values=values, errors=errors)
        if form_msg is not None:
            ctx.update_ui(form_msg, [card])
        else:
            ask_details(ctx, values=values, errors=errors)
        return

    if form_msg is not None:
        ctx.update_ui(form_msg, [cards.details_form_card(ctx.lang, values=values, state="done")])
    _echo(ctx, f"{name}, {phone}")
    state = ctx.set_demo_state(name=name, phone=phone)
    service = _state_service(ctx)
    start = _parse_dt(state.get("slot"))
    if service is None or start is None:
        ask_service(ctx)
        return
    propose(ctx, service, start, name, phone)


def _on_modify(ctx: ChatContext, action: dict) -> None:
    _echo(ctx, t("modify", ctx.lang))
    options = [
        cards.option(t(key, ctx.lang), cards.send_action("modify_choice", t(key, ctx.lang), value=value))
        for key, value in (("modify_service", "service"), ("modify_time", "time"), ("modify_contact", "contact"))
    ]
    _say(ctx, t("modify_ask", ctx.lang), [cards.quick_replies(options)])


def _on_modify_choice(ctx: ChatContext, action: dict) -> None:
    value = str(action.get("value"))
    keys = {"service": "modify_service", "time": "modify_time", "contact": "modify_contact"}
    if value not in keys:
        _fallback(ctx)
        return
    _echo(ctx, t(keys[value], ctx.lang))
    flow.drop_pending(ctx)
    service = _state_service(ctx)
    if value == "service" or service is None:
        ask_service(ctx)  # keeps the name and phone already given
    elif value == "time":
        ask_day(ctx, service)
    else:
        state = ctx.demo_state
        values = {"name": state.get("name") or "", "phone": state.get("phone") or ""}
        ctx.set_demo_state(name=None, phone=None)
        ask_details(ctx, values=values)


def _on_start_demo(ctx: ChatContext, action: dict) -> None:
    ctx.session.mode = "demo"
    _echo(ctx, t("start_booking", ctx.lang))
    ask_service(ctx)


_HANDLERS = {
    "menu": _on_menu,
    "service": _on_service,
    "day": _on_day,
    "other_day": _on_other_day,
    "slot": _on_slot,
    "details": _on_details,
    "confirm": confirm,
    "modify": _on_modify,
    "modify_choice": _on_modify_choice,
    "start_demo": _on_start_demo,
}
