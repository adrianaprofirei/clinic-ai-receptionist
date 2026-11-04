"""Reception dashboard: login, week data, cancellation, live stream."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import auth
from app import scheduling as sch
from app.clinic import SERVICES_BY_ID
from app.config import Settings
from app.routers.admin import login_guard

from .helpers import ChatClient, make_app

PASSWORD = "test-password"


@pytest.fixture(autouse=True)
def _reset_login_guard():
    login_guard._failures.clear()
    yield
    login_guard._failures.clear()


@pytest.fixture
def env(tmp_path):
    app, clock = make_app(tmp_path)
    with TestClient(app) as http:
        yield app, clock, http


def login(http, password=PASSWORD):
    return http.post("/admin/login", data={"password": password}, follow_redirects=False)


def add_booking(app, clock, service="detartraj", start=datetime(2026, 11, 5, 11), source="demo", name="Andreea Popescu"):
    with app.state.session_factory() as db:
        return sch.create_booking(
            db,
            sch.NewBooking(service=SERVICES_BY_ID[service], start=start, name=name, phone="0712 345 678", source=source),
            now=clock.now_local(),
            created_at=clock.utc_naive(),
            allow_past=True,
        )


def test_admin_shows_the_login_form_first(env):
    _app, _clock, http = env
    html = http.get("/admin").text
    assert "Panoul recepției" in html and 'name="password"' in html
    assert "Demo – clinică fictivă" in html
    assert "noindex" in html


def test_login_sets_a_strict_httponly_cookie_and_opens_the_dashboard(env):
    _app, _clock, http = env
    response = login(http)
    assert response.status_code == 303 and response.headers["location"] == "/admin"
    cookie = response.headers["set-cookie"]
    assert "zb_admin=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    dashboard = http.get("/admin").text
    assert "data-admin" in dashboard and "Demo – clinică fictivă" in dashboard


def test_wrong_passwords_count_down_then_lock_for_15_minutes(env):
    _app, _clock, http = env
    for left in (4, 3, 2, 1):
        response = login(http, "wrong")
        assert response.status_code == 401 and f"Mai ai {left} încerc" in response.text
    assert "Accesul s-a blocat" in login(http, "wrong").text
    locked = login(http)  # even the right password is refused now
    assert locked.status_code == 429 and "Încearcă din nou peste 15 minute" in locked.text


def test_every_admin_api_requires_a_session(env):
    _app, _clock, http = env
    assert http.get("/api/admin/week").status_code == 401
    assert http.post("/api/admin/bookings/1/cancel").status_code == 401
    assert http.get("/api/admin/stream").status_code == 401


def test_tokens_cannot_be_forged_and_die_with_a_password_change():
    settings = Settings(admin_password="a-password", secret_key="k")
    token = auth.make_token(settings, now_ts=1_000)
    assert auth.token_valid(settings, token, now_ts=1_001)
    assert not auth.token_valid(settings, token, now_ts=1_000 + auth.SESSION_HOURS * 3600 + 1)  # expired
    expires, _sig = token.split(".")
    assert not auth.token_valid(settings, f"{int(expires) + 999}.{_sig}", now_ts=1_001)  # tampered
    assert not auth.token_valid(replace(settings, admin_password="new-password"), token, now_ts=1_001)


def test_week_view_data(env):
    app, clock, http = env
    login(http)
    new = add_booking(app, clock)  # Thursday 11:00, via the assistant
    add_booking(app, clock, service="consultatie", start=datetime(2026, 11, 2, 9), source="seed", name="Ioana M.")
    data = http.get("/api/admin/week").json()

    assert data["week_start"] == "2026-11-02" and data["label"] == "2 – 7 noiembrie 2026"
    assert [d["weekday"] for d in data["days"]] == ["Lun", "Mar", "Mie", "Joi", "Vin", "Sâm"]
    assert [d["is_today"] for d in data["days"]] == [False, False, True, False, False, False]
    assert data["days"][5]["closes"] == "13:00"
    assert data["stats"] == {"today": 0, "tomorrow": 1, "week": 2}

    thursday = next(b for b in data["bookings"] if b["id"] == new.id)
    assert thursday["is_new"] and thursday["can_cancel"] and not thursday["is_past"]
    assert (thursday["start"], thursday["end"], thursday["start_min"]) == ("11:00", "11:45", 660)
    assert thursday["source_label"] == "Mod demo, chat pe site"
    assert thursday["date_short"] == "Joi, 5 nov" and thursday["tel"] == "+40712345678"
    monday = next(b for b in data["bookings"] if b["source"] == "seed")
    assert monday["is_past"] and not monday["can_cancel"] and not monday["is_new"]


def test_week_navigation_snaps_to_monday(env):
    _app, _clock, http = env
    login(http)
    data = http.get("/api/admin/week", params={"start": "2026-11-11"}).json()
    assert data["week_start"] == "2026-11-09"
    assert (data["prev"], data["next"], data["this_week"]) == ("2026-11-02", "2026-11-16", "2026-11-02")
    assert http.get("/api/admin/week", params={"start": "not-a-date"}).status_code == 400


def test_on_sunday_the_dashboard_opens_on_the_coming_week(tmp_path):
    app, _clock = make_app(tmp_path, now=datetime(2026, 11, 8, 10, 0))
    with TestClient(app) as http:
        login(http)
        assert http.get("/api/admin/week").json()["week_start"] == "2026-11-09"


def test_cancelling_frees_the_slot_and_notifies_listeners(env):
    app, clock, http = env
    login(http)
    booking = add_booking(app, clock)
    response = http.post(f"/api/admin/bookings/{booking.id}/cancel")
    assert response.status_code == 200 and response.json()["booking"]["status"] == "cancelled"
    assert app.state.bus.recent[-1]["type"] == "booking.cancelled"
    with app.state.session_factory() as db:
        free = sch.available_starts(db, SERVICES_BY_ID["detartraj"], booking.start.date(), clock.now_local())
    assert booking.start in free

    again = http.post(f"/api/admin/bookings/{booking.id}/cancel")
    assert again.status_code == 409 and again.json()["error"] == "already_cancelled"
    assert http.post("/api/admin/bookings/99999/cancel").status_code == 404


def test_finished_appointments_cannot_be_cancelled(env):
    app, clock, http = env
    login(http)
    past = add_booking(app, clock, start=datetime(2026, 11, 2, 10))
    response = http.post(f"/api/admin/bookings/{past.id}/cancel")
    assert response.status_code == 409 and response.json()["error"] == "already_finished"


def test_cross_site_requests_are_rejected(env):
    app, clock, http = env
    login(http)
    booking = add_booking(app, clock)
    response = http.post(f"/api/admin/bookings/{booking.id}/cancel", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert http.post("/admin/login", data={"password": PASSWORD}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_logout_ends_the_session(env):
    _app, _clock, http = env
    login(http)
    response = http.post("/admin/logout", follow_redirects=False)
    assert response.status_code == 303 and 'zb_admin=""' in response.headers["set-cookie"]
    http.cookies.clear()
    assert http.get("/api/admin/week").status_code == 401


def test_a_booking_made_in_the_chat_shows_up_in_the_admin_week(env):
    app, _clock, http = env
    chat = ChatClient(http)
    chat.open()
    chat.act("menu", value="book")
    days = chat.act("service", value="consultatie").card("days")
    slots = chat.send(days["options"][0]["send"]).card("slots")
    form_id = chat.send(slots["options"][0]["send"]).messages[-1]["id"]
    summary = chat.act("details", name="Maria Ionescu", phone="0744 111 222", message_id=form_id).card("summary")
    code = chat.send(summary["confirm"]["send"]).card("booking")["code"]

    login(http)
    booking = next(b for b in http.get("/api/admin/week").json()["bookings"] if b["code"] == code)
    assert booking["patient_name"] == "Maria Ionescu" and booking["is_new"] and booking["source"] == "demo"
    event = app.state.bus.recent[-1]
    assert event["type"] == "booking.created" and event["booking"]["when_label"] == "miercuri, 12:00"


def test_live_stream_pushes_booking_events(env):
    app, clock, http = env
    login(http)
    app.state.sse_heartbeat_seconds = 0.2
    received: list[str] = []

    def publish_soon():
        time.sleep(0.5)
        add_booking(app, clock, service="plomba", start=datetime(2026, 11, 6, 10))
        app.state.bus.publish({"type": "booking.created", "booking": {"id": 1, "source": "ai"}})

    threading.Thread(target=publish_soon, daemon=True).start()
    with http.stream("GET", "/api/admin/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        lines = response.iter_lines()
        for line in lines:
            received.append(line)
            if line == "event: booking.created":
                received.append(next(lines))  # its data line
                break
            assert len(received) < 200, "no booking event received"
    assert "event: hello" in received
    assert ": ping" in received  # heartbeats keep proxies from closing the stream
    assert json.loads(received[-1].removeprefix("data: "))["booking"]["source"] == "ai"
