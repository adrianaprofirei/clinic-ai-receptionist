"""The chat API in scripted demo mode (no OPENAI_API_KEY)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import scheduling as sch
from app.clinic import SERVICES_BY_ID
from app.models import Booking, ChatSession

from .helpers import ChatClient, make_app


@pytest.fixture
def env(tmp_path):
    app, clock = make_app(tmp_path)
    with TestClient(app) as http:
        yield app, clock, ChatClient(http)


def db_bookings(app) -> list[Booking]:
    with app.state.session_factory() as db:
        return list(db.scalars(select(Booking).order_by(Booking.id)))


def book_until_summary(chat: ChatClient, *, service="albire", day_top="Sâm", slot_index=0,
                       name="andreea popescu", phone="0712345678"):
    chat.open()
    chat.act("menu", value="book")
    days = chat.act("service", value=service).card("days")
    day = next(o for o in days["options"] if o["top"] == day_top)
    slots = chat.send(day["send"]).card("slots")
    form_turn = chat.send(slots["options"][slot_index]["send"])
    form_id = form_turn.messages[-1]["id"]
    return chat.act("details", name=name, phone=phone, message_id=form_id), slots["options"][slot_index]


def test_session_starts_in_demo_mode_with_a_greeting_and_cookie(env):
    _app, _clock, chat = env
    data = chat.open()
    assert data["session"]["mode"] == "demo" and data["session"]["ai_available"] is False
    assert data["session"]["max_chars"] == 500
    greeting = data["messages"][0]
    assert "modul demo" in greeting["text"]
    labels = [o["label"] for o in greeting["ui"][0]["options"]]
    assert labels == ["Vreau o programare", "Prețuri", "Program și adresă", "Am dureri"]

    cookie = next(c for c in chat.http.cookies.jar if c.name == "zb_chat")
    assert cookie.path == "/api/chat"
    assert cookie.has_nonstandard_attr("HttpOnly")


def test_history_is_kept_per_browser(env, tmp_path):
    app, _clock, chat = env
    chat.open()
    chat.act("menu", value="prices")
    again = chat.open()
    assert [m["text"] for m in again["messages"]][-2:] == ["Prețuri", "Prețurile noastre (demonstrative, clinică fictivă):"]

    with TestClient(app) as other_browser:
        fresh = ChatClient(other_browser).open()
    assert len(fresh["messages"]) == 1  # only the greeting


def test_full_scripted_booking_flow(env):
    app, _clock, chat = env
    turn, slot = book_until_summary(chat)

    summary = turn.card("summary")
    assert turn.texts[-1].startswith("Mulțumesc, Andreea!")
    rows = {r["label"]: r["value"] for r in summary["rows"]}
    assert rows["Serviciu"] == "Albire · 90 min"
    assert rows["Când"].startswith("Sâmbătă, 7 noiembrie, ")
    assert rows["Nume"] == "Andreea Popescu" and rows["Telefon"] == "0712 345 678"
    assert rows["Preț"] == "1.200 lei, plătești la clinică"
    assert db_bookings(app) == []  # nothing is written before the explicit confirmation

    done = chat.send(summary["confirm"]["send"])
    booking_card = done.card("booking")
    [booking] = db_bookings(app)
    assert booking.code == booking_card["code"]
    assert (booking.source, booking.patient_name, booking.phone) == ("demo", "Andreea Popescu", "0712 345 678")
    assert booking.start == datetime.fromisoformat(slot["value"])
    assert "BEGIN:VEVENT" in booking_card["calendar"]["ics"]
    assert done.card("summary", include_updates=True)["state"] == "confirmed"
    assert app.state.bus.recent[-1]["type"] == "booking.created"
    assert done.session["demo_step"] == 0


def test_demo_steps_are_reported_for_the_header(env):
    _app, _clock, chat = env
    chat.open()
    assert chat.act("menu", value="book").session["demo_step"] == 1
    days = chat.act("service", value="consultatie")
    assert days.session["demo_step"] == 2
    slots = chat.send(days.card("days")["options"][0]["send"])
    assert slots.session["demo_step"] == 3
    assert chat.send(slots.card("slots")["options"][0]["send"]).session["demo_step"] == 4


def test_consent_is_shown_before_name_and_phone(env):
    app, _clock, chat = env
    chat.open()
    chat.act("menu", value="book")
    days = chat.act("service", value="consultatie").card("days")
    slots = chat.send(days["options"][0]["send"]).card("slots")
    form = chat.send(slots["options"][0]["send"]).card("details_form")
    assert "24 de ore" in form["consent"]
    assert [f["name"] for f in form["fields"]] == ["name", "phone"]
    with app.state.session_factory() as db:
        assert db.scalars(select(ChatSession)).one().consent_shown is True


def test_invalid_details_update_the_form_without_booking(env):
    app, _clock, chat = env
    turn, _slot = book_until_summary(chat, phone="0712 34")
    update = turn.of("update")[-1]["message"]["ui"][0]
    assert update["errors"]["phone"] == "Numărul pare incomplet. Exemplu: 07xx xxx xxx"
    assert update["values"]["name"] == "Andreea Popescu"
    assert not turn.of("user") and not turn.cards("summary")
    assert db_bookings(app) == []


def test_slot_taken_before_confirmation_offers_the_nearest_times(env):
    app, clock, chat = env
    turn, slot = book_until_summary(chat, service="consultatie", day_top="Joi", slot_index=2)
    summary = turn.card("summary")
    start = datetime.fromisoformat(slot["value"])
    with app.state.session_factory() as db:  # another visitor books the same time meanwhile
        sch.create_booking(
            db,
            sch.NewBooking(service=SERVICES_BY_ID["consultatie"], start=start, name="Ion Pop",
                           phone="0722 000 000", source="ai"),
            now=clock.now_local(),
            created_at=clock.utc_naive(),
        )

    result = chat.send(summary["confirm"]["send"])
    assert "tocmai s-a ocupat" in result.texts[-1]
    alternatives = result.card("slots")["options"]
    assert 1 <= len(alternatives) <= 3 and slot["label"] not in [o["label"] for o in alternatives]
    assert result.card("summary", include_updates=True)["state"] == "expired"
    assert [b.patient_name for b in db_bookings(app)] == ["Ion Pop"]

    # Picking an alternative goes straight back to a summary: name and phone are remembered.
    again = chat.send(alternatives[0]["send"])
    assert again.card("summary")["rows"][2]["value"] == "Andreea Popescu"


def test_summary_expires_after_15_minutes(env):
    app, clock, chat = env
    turn, _slot = book_until_summary(chat, service="consultatie", day_top="Joi")
    clock.advance(minutes=16)
    result = chat.send(turn.card("summary")["confirm"]["send"])
    assert "a expirat" in result.texts[0]
    assert result.cards("slots")
    assert db_bookings(app) == []


def test_double_click_on_confirm_books_once(env):
    app, _clock, chat = env
    turn, _slot = book_until_summary(chat)
    confirm = turn.card("summary")["confirm"]["send"]
    chat.send(confirm)
    second = chat.send(confirm)
    assert second.texts == [f"Programarea e deja confirmată. Codul tău: {db_bookings(app)[0].code}."]
    assert len(db_bookings(app)) == 1


def test_bookings_per_chat_session_are_capped(tmp_path):
    app, _clock = make_app(tmp_path, max_bookings_per_session=1)
    with TestClient(app) as http:
        chat = ChatClient(http)
        turn, _slot = book_until_summary(chat)
        chat.send(turn.card("summary")["confirm"]["send"])
        second, _slot = book_until_summary(chat, service="consultatie", day_top="Joi")
    assert "cel mult 1 programări" in second.texts[-1]
    assert len(db_bookings(app)) == 1


def test_modify_contact_reopens_a_prefilled_form(env):
    _app, _clock, chat = env
    turn, _slot = book_until_summary(chat)
    choices = chat.send(turn.card("summary")["modify"]["send"]).card("quick_replies")
    assert [o["label"] for o in choices["options"]] == ["Serviciul", "Ziua sau ora", "Numele sau telefonul"]
    form = chat.send(choices["options"][2]["send"]).card("details_form")
    assert form["values"] == {"name": "Andreea Popescu", "phone": "0712 345 678"}


def test_pain_shows_the_emergency_card(env):
    _app, _clock, chat = env
    chat.open()
    turn = chat.act("menu", value="pain")
    emergency = turn.card("emergency")
    assert emergency["call"]["tel"] == "+40232000000"
    assert "112" in emergency["warning"]
    assert emergency["hours"] == "Deschis azi până la 19:00 · Sâm 09:00–13:00"
    assert "Nu pot oferi sfaturi medicale" in turn.texts[0]
    earliest = turn.card("quick_replies")["options"][0]
    slots = chat.send(earliest["send"]).card("slots")
    assert slots["date"] == date(2026, 11, 4).isoformat()  # still room today


def test_prices_list_every_service(env):
    _app, _clock, chat = env
    chat.open()
    rows = chat.act("menu", value="prices").card("price_list")["rows"]
    assert [(r["label"], r["value"]) for r in rows] == [
        ("Consultație", "150 lei"),
        ("Detartraj și periaj profesional", "250 lei"),
        ("Albire profesională", "1.200 lei"),
        ("Plombă", "300 lei"),
        ("Extracție", "350 lei"),
        ("Consultație pentru implant", "200 lei"),
    ]


def test_free_text_in_demo_mode_points_to_the_buttons_in_the_visitors_language(env):
    _app, _clock, chat = env
    chat.open()
    assert chat.say("Bună, aveți loc mâine?").texts == ["În modul demo alegi din butoane. Cu ce te pot ajuta?"]
    english = chat.say("Hi, do you have a free slot tomorrow?")
    assert english.texts == ["In demo mode you choose from the buttons. How can I help?"]
    assert english.card("quick_replies")["options"][0]["label"] == "Book an appointment"


def test_message_length_and_empty_messages_are_rejected(env):
    _app, _clock, chat = env
    chat.open()
    too_long = chat.say("x" * 501)
    assert too_long.status == 413 and too_long.error["error"] == "too_long"
    assert chat.say("x" * 500).status == 200
    assert chat.say("   ").status == 400


def test_messages_are_rate_limited_per_ip(tmp_path):
    app, _clock = make_app(tmp_path, rate_limit_messages=2, rate_limit_window_seconds=600)
    with TestClient(app) as http:
        chat = ChatClient(http)
        chat.open()
        assert chat.say("Bună").status == 200
        assert chat.say("Bună").status == 200
        limited = chat.say("Bună")
    assert limited.status == 429
    assert limited.error["error"] == "rate_limited" and limited.error["retry_after"] > 0
    assert "0232 000 000" in limited.error["message"]


def test_unknown_or_oversized_actions_are_rejected(env):
    _app, _clock, chat = env
    chat.open()
    assert chat._post({"action": {"no_type": 1}}).status == 400
    assert chat._post({"action": {"type": "menu", "value": "x" * 3000}}).status == 400
    assert chat.act("does_not_exist").texts[0].startswith("Scuze")
