"""Slot generation, opening hours and double-booking prevention."""

from __future__ import annotations

import re
import threading
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from app import scheduling as sch
from app.clinic import SERVICES_BY_ID
from app.models import Booking, SlotClaim

from .conftest import CREATED, NOW

CONSULT = SERVICES_BY_ID["consultatie"]  # 30 min
SCALING = SERVICES_BY_ID["detartraj"]  # 45 min
WHITENING = SERVICES_BY_ID["albire"]  # 90 min

THU = date(2026, 11, 5)
SAT = date(2026, 11, 7)
SUN = date(2026, 11, 8)


def at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, sch.parse_time(hhmm))


def book(db, service, start, *, name="Andreea Popescu", phone="0712 345 678", now=NOW):
    return sch.create_booking(
        db,
        sch.NewBooking(service=service, start=start, name=name, phone=phone, source="demo"),
        now=now,
        created_at=CREATED,
    )


def times(starts):
    return [s.strftime("%H:%M") for s in starts]


# --- slot generation -------------------------------------------------------------


class TestSlotGeneration:
    def test_weekday_grid_fits_service_inside_opening_hours(self, db):
        starts = sch.available_starts(db, CONSULT, THU, NOW)
        assert times(starts)[0] == "09:00"
        assert times(starts)[-1] == "18:30"  # 18:30 + 30 min = 19:00 closing
        assert len(starts) == (600 - 30) // 15 + 1
        assert all(s.minute % 15 == 0 for s in starts)

    def test_slot_length_equals_service_duration(self, db):
        last = sch.available_starts(db, WHITENING, THU, NOW)[-1]
        assert last.time() == time(17, 30)  # 17:30 + 90 min = 19:00

    def test_saturday_closes_at_13(self, db):
        starts = sch.available_starts(db, WHITENING, SAT, NOW)
        assert times(starts)[0] == "09:00"
        assert times(starts)[-1] == "11:30"  # 11:30 + 90 min = 13:00

    def test_sunday_is_closed(self, db):
        assert sch.available_starts(db, CONSULT, SUN, NOW) == []

    def test_today_hides_past_times_and_applies_lead_time(self, db):
        starts = sch.available_starts(db, CONSULT, NOW.date(), NOW)
        # 11:20 + 30 min lead = 11:50, rounded up to the grid.
        assert times(starts)[0] == "12:00"

    def test_past_days_have_no_slots(self, db):
        assert sch.available_starts(db, CONSULT, NOW.date() - timedelta(days=1), NOW) == []

    def test_no_slots_beyond_the_booking_horizon(self, db):
        far = NOW.date() + timedelta(days=sch.HORIZON_DAYS + 1)
        assert sch.available_starts(db, CONSULT, far, NOW) == []

    def test_booked_time_is_removed_but_adjacent_slots_stay(self, db):
        book(db, SCALING, at(THU, "10:00"))  # 10:00-10:45
        starts = times(sch.available_starts(db, CONSULT, THU, NOW))
        assert "09:30" in starts  # ends exactly at 10:00
        assert "10:45" in starts  # starts exactly at 10:45
        for blocked in ("09:45", "10:00", "10:15", "10:30"):
            assert blocked not in starts

    def test_cancelled_booking_frees_its_slot(self, db):
        booking = book(db, SCALING, at(THU, "10:00"))
        sch.cancel_booking(db, booking, by="admin", now=NOW, cancelled_at=CREATED)
        assert "10:00" in times(sch.available_starts(db, CONSULT, THU, NOW))

    def test_next_available_skips_full_and_closed_days(self, db):
        # Saturday 7 Nov is fully booked for whitening, Sunday is closed.
        sat_now = datetime(2026, 11, 7, 8, 0)
        book(db, WHITENING, at(SAT, "09:00"), now=sat_now)
        book(db, WHITENING, at(SAT, "10:30"), now=sat_now)
        day, starts = sch.next_available(db, WHITENING, sat_now)
        assert day == date(2026, 11, 9)  # Monday
        assert times(starts)[0] == "09:00"

    def test_suggestions_are_spread_and_prefer_round_times(self):
        starts = sch.grid_starts(THU, 45)
        picked = sch.suggest(starts, 6)
        assert len(picked) == 6
        assert all(s.minute in (0, 30) for s in picked)
        assert picked[0].time() == time(9, 0) and picked[-1].hour >= 17

    def test_free_ranges_group_consecutive_times(self):
        starts = [at(THU, t) for t in ("09:00", "09:15", "09:30", "11:00", "11:15")]
        ranges = sch.free_ranges(starts)
        assert [(times([a])[0], times([b])[0]) for a, b in ranges] == [("09:00", "09:30"), ("11:00", "11:15")]


# --- opening hours and other validation --------------------------------------------


class TestValidation:
    @pytest.mark.parametrize(
        ("day", "hhmm", "service", "code"),
        [
            (THU, "08:45", CONSULT, "outside_hours"),  # before opening
            (THU, "18:45", CONSULT, "outside_hours"),  # would end at 19:15
            (THU, "17:45", WHITENING, "outside_hours"),  # 90 min would end at 19:15
            (SAT, "12:00", WHITENING, "outside_hours"),  # Saturday closes at 13:00
            (SAT, "13:00", CONSULT, "outside_hours"),
            (SUN, "10:00", CONSULT, "closed_day"),
            (THU, "10:10", CONSULT, "misaligned"),
        ],
    )
    def test_rejects_times_outside_the_rules(self, db, day, hhmm, service, code):
        with pytest.raises(sch.BookingError) as err:
            book(db, service, at(day, hhmm))
        assert err.value.code == code

    def test_outside_hours_error_reports_latest_start(self, db):
        with pytest.raises(sch.BookingError) as err:
            book(db, WHITENING, at(SAT, "12:00"))
        assert err.value.details["latest"] == time(11, 30)

    def test_rejects_past_dates_and_times(self, db):
        for start in (at(NOW.date() - timedelta(days=1), "10:00"), at(NOW.date(), "10:00")):
            with pytest.raises(sch.BookingError) as err:
                book(db, CONSULT, start)
            assert err.value.code == "in_past"

    def test_rejects_times_inside_the_lead_time(self, db):
        with pytest.raises(sch.BookingError) as err:
            book(db, CONSULT, at(NOW.date(), "11:30"))  # in the future, but < 30 min away
        assert err.value.code == "too_soon"

    def test_rejects_dates_beyond_the_horizon(self, db):
        with pytest.raises(sch.BookingError) as err:
            book(db, CONSULT, at(date(2026, 12, 21), "10:00"))
        assert err.value.code == "too_far"

    def test_accepts_last_slot_of_the_day(self, db):
        booking = book(db, CONSULT, at(THU, "18:30"))
        assert booking.end == at(THU, "19:00")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0712345678", "0712 345 678"),
            ("0712 345 678", "0712 345 678"),
            ("+40 712-345-678", "0712 345 678"),
            ("0040712345678", "0712 345 678"),
            ("(0232) 000.000", "0232 000 000"),
            ("+44 20 7946 0958", "+442079460958"),
        ],
    )
    def test_phone_numbers_are_normalised(self, raw, expected):
        assert sch.normalize_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["0712 34", "0812345678", "abc", "", "+0123"])
    def test_invalid_phone_numbers_are_rejected(self, raw):
        with pytest.raises(sch.BookingError) as err:
            sch.normalize_phone(raw)
        assert err.value.code == "invalid_phone"

    def test_names_are_cleaned_and_validated(self):
        assert sch.normalize_name("  andreea   popescu ") == "Andreea Popescu"
        assert sch.normalize_name("Ștefan Mărgineanu-Ionescu") == "Ștefan Mărgineanu-Ionescu"
        for bad in ("A", "John 123", "x" * 61, "http://spam"):
            with pytest.raises(sch.BookingError):
                sch.normalize_name(bad)

    def test_time_parsing_accepts_common_formats(self):
        assert sch.parse_time("9") == time(9, 0)
        assert sch.parse_time("11.30") == time(11, 30)
        assert sch.parse_time("17h15") == time(17, 15)
        with pytest.raises(sch.BookingError):
            sch.parse_time("25:00")


# --- double booking ---------------------------------------------------------------------


class TestDoubleBooking:
    def test_overlapping_booking_is_rejected(self, db):
        book(db, SCALING, at(THU, "11:00"))  # 11:00-11:45
        with pytest.raises(sch.BookingError) as err:
            book(db, CONSULT, at(THU, "11:30"), name="Ion Pop")
        assert err.value.code == "slot_taken"

    def test_same_start_is_rejected(self, db):
        book(db, CONSULT, at(THU, "11:00"))
        with pytest.raises(sch.BookingError):
            book(db, CONSULT, at(THU, "11:00"), name="Ion Pop")

    def test_back_to_back_bookings_are_allowed(self, db):
        book(db, SCALING, at(THU, "11:00"))
        second = book(db, CONSULT, at(THU, "11:45"), name="Ion Pop")
        assert second.start == at(THU, "11:45")

    def test_database_constraint_blocks_overlaps_even_without_the_check(self, db, session_factory, monkeypatch):
        book(db, SCALING, at(THU, "11:00"))
        calls = {"n": 0}
        real = sch.busy_intervals

        def blind_first_check(*args, **kwargs):  # simulate a request that raced past the check
            calls["n"] += 1
            return [] if calls["n"] == 1 else real(*args, **kwargs)

        monkeypatch.setattr(sch, "busy_intervals", blind_first_check)
        with session_factory() as other_request, pytest.raises(sch.BookingError) as err:
            book(other_request, CONSULT, at(THU, "11:30"), name="Ion Pop")
        assert err.value.code == "slot_taken"
        assert db.scalar(select(func.count()).select_from(Booking)) == 1

    def test_concurrent_requests_for_one_slot_produce_one_booking(self, session_factory):
        barrier = threading.Barrier(4)
        outcomes: list[str] = []
        lock = threading.Lock()

        def attempt(i: int) -> None:
            with session_factory() as session:
                barrier.wait()
                try:
                    book(session, SCALING, at(THU, "15:00"), name=f"Pacient {'abcd'[i]}")
                    result = "ok"
                except sch.BookingError as exc:
                    result = exc.code
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(outcomes) == ["ok", "slot_taken", "slot_taken", "slot_taken"]
        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Booking)) == 1
            assert session.scalar(select(func.count()).select_from(SlotClaim)) == 3  # 45 min = 3 blocks


# --- codes and cancellation ---------------------------------------------------------------


class TestCancellation:
    def test_booking_codes_are_short_and_unambiguous(self, db):
        booking = book(db, CONSULT, at(THU, "09:00"))
        assert re.fullmatch(r"ZB-[2-9A-HJ-NP-Z]{4}", booking.code)
        assert sch.find_by_code(db, booking.code.lower().replace("-", "")) == booking

    def test_cancel_releases_the_slot_for_rebooking(self, db):
        booking = book(db, SCALING, at(THU, "11:00"))
        sch.cancel_booking(db, booking, by="patient", now=NOW, cancelled_at=CREATED)
        assert booking.status == "cancelled"
        assert db.scalar(select(func.count()).select_from(SlotClaim)) == 0
        assert book(db, SCALING, at(THU, "11:00"), name="Ion Pop").status == "confirmed"

    def test_cannot_cancel_twice(self, db):
        booking = book(db, CONSULT, at(THU, "09:00"))
        sch.cancel_booking(db, booking, by="admin", now=NOW, cancelled_at=CREATED)
        with pytest.raises(sch.BookingError) as err:
            sch.cancel_booking(db, booking, by="admin", now=NOW, cancelled_at=CREATED)
        assert err.value.code == "already_cancelled"

    def test_cannot_cancel_a_finished_appointment(self, db):
        booking = book(db, CONSULT, at(THU, "09:00"))
        later = at(THU, "10:00")
        with pytest.raises(sch.BookingError) as err:
            sch.cancel_booking(db, booking, by="admin", now=later, cancelled_at=CREATED)
        assert err.value.code == "already_finished"
