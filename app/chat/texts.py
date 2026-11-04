"""Every sentence the server itself writes in the chat, in Romanian and English.

The LLM writes free-form replies; these strings cover the scripted demo, the
cards, guardrail answers and error messages, so they stay consistent and cost
no tokens.
"""

from __future__ import annotations

from ..i18n import fmt_time, norm_lang
from ..scheduling import BookingError

TEXTS: dict[str, dict[str, str]] = {
    "ro": {
        # greeting and menu
        "greeting": "Bună! Sunt asistentul Clinicii Zâmbet. Te ajut cu prețuri, program sau o programare.",
        "greeting_demo": (
            "Bună! Sunt asistentul Clinicii Zâmbet. Acum funcționez în modul demo: "
            "alegi din butoane și te programez în patru pași."
        ),
        "qr_book": "Vreau o programare",
        "qr_prices": "Prețuri",
        "qr_hours": "Program și adresă",
        "qr_pain": "Am dureri",
        "qr_directions": "Cum ajung la clinică?",
        "qr_thanks": "Mulțumesc!",
        "qr_earliest": "Da, cea mai apropiată oră",
        "qr_no_thanks": "Nu, mulțumesc",
        "qr_new_booking": "Altă programare",
        # booking steps
        "ask_service": "Pentru ce serviciu vrei programare?",
        "ask_day": "Ce zi preferi pentru {phrase}?",
        "days_label": "Alege ziua",
        "slots_intro": "{day} lucrăm {opens}–{closes}, deci pentru {phrase} ({duration} min) am liber la:",
        "slots_intro_today": "Azi lucrăm până la {closes}, deci pentru {phrase} ({duration} min) mai am liber la:",
        "slots_label": "Ore libere {day}",
        "other_day": "Altă zi",
        "more_times": "Arată toate orele",
        "no_days": (
            "Nu mai am ore libere pentru {phrase} în următoarele 30 de zile. "
            "Sună-ne la {phone} și găsim o soluție."
        ),
        "day_full": "În ziua aleasă nu mai am loc pentru {phrase}. Alege, te rog, altă zi:",
        "earliest_intro": "Cea mai apropiată zi cu loc pentru o consultație e {day}. Am liber la:",
        # personal data
        "consent": (
            "Pentru programare îți cer numele și telefonul. Le folosim doar pentru această programare "
            "și se șterg automat după 24 de ore."
        ),
        "consent_form": (
            "Continuând, ești de acord să folosim numele și telefonul doar pentru această programare. "
            "Se șterg automat după 24 de ore."
        ),
        "consent_link": "Cum folosim datele",
        "name_label": "Nume și prenume",
        "name_placeholder": "ex. Andreea Popescu",
        "phone_label": "Telefon",
        "phone_placeholder": "07xx xxx xxx",
        "continue": "Continuă",
        "details_sent": "Date trimise",
        # summary and confirmation
        "summary_intro": "Mulțumesc, {first}! Verifică, te rog, detaliile înainte să fac programarea:",
        "summary_title": "Rezumatul programării",
        "row_service": "Serviciu",
        "row_when": "Când",
        "row_name": "Nume",
        "row_phone": "Telefon",
        "row_price": "Preț",
        "row_for": "Pe numele",
        "row_address": "Adresă",
        "row_code": "Cod",
        "price_at_clinic": "{price}, plătești la clinică",
        "confirm": "Confirm programarea",
        "modify": "Vreau să modific ceva",
        "confirmed_label": "Ai confirmat",
        "superseded_label": "Înlocuit de un rezumat nou",
        "expired_label": "Nu mai e valabil",
        "booked_title": "Programare confirmată",
        "add_calendar": "Adaugă în calendar",
        "calendar_title": "{service} · {clinic}",
        "calendar_description": "Cod programare {code}. Telefon clinică {phone}. Demo – clinică fictivă.",
        "booked_followup_ai": (
            "Te așteptăm! Dacă nu mai poți ajunge, scrie-mi aici și anulez eu programarea, "
            "sau sună-ne la {phone}."
        ),
        "booked_followup_demo": "Te așteptăm! Dacă nu mai poți ajunge, sună-ne la {phone} și anulăm programarea.",
        "booked_note_llm": (
            "[Notă sistem: vizitatorul a apăsat „Confirm programarea”. Programare confirmată: cod {code}, "
            "{service}, {when}, pe numele {name}.]"
        ),
        "modify_ask": "Sigur. Ce vrei să modifici?",
        "modify_service": "Serviciul",
        "modify_time": "Ziua sau ora",
        "modify_contact": "Numele sau telefonul",
        "pending_missing": "Nu mai găsesc rezumatul programării. Hai s-o luăm de la capăt.",
        "already_confirmed": "Programarea e deja confirmată. Codul tău: {code}.",
        "pending_expired": "Rezumatul a expirat după 15 minute. Hai să verificăm din nou ora.",
        "slot_taken": "Ora {time} tocmai s-a ocupat. Cele mai apropiate ore libere {day}:",
        "slot_taken_none": "Ora {time} tocmai s-a ocupat și nu mai am alte ore libere {day}. Alege altă zi:",
        "too_many_bookings": (
            "Din acest chat poți face cel mult {n} programări active. Pentru altele, sună-ne la {phone}."
        ),
        # other answers
        "prices_intro": "Prețurile noastre (demonstrative, clinică fictivă):",
        "prices_note": "Implantul se discută la consultație; programăm doar consultația.",
        "hours_reply": (
            "Lucrăm luni–vineri 09:00–19:00 și sâmbătă 09:00–13:00; duminica e închis. "
            "Ne găsești pe {address} (adresă fictivă), telefon {phone}."
        ),
        "directions": (
            "Ne găsești pe {address} (adresă fictivă, pentru demo). Pe pagină, la „Program și adresă”, "
            "poți deschide și harta."
        ),
        "thanks_reply": "Cu drag! Te așteptăm.",
        "no_thanks_reply": "Bine. Dacă te răzgândești, sunt aici.",
        "pain_reply": (
            "Îmi pare rău că te doare. Nu pot oferi sfaturi medicale, dar durerea sau umflătura "
            "trebuie văzută de un medic cât mai repede. Sună-ne acum:"
        ),
        "pain_offer": "Vrei să-ți caut și cea mai apropiată oră liberă?",
        "emergency_call": "Sună la {phone}",
        "emergency_open": "Deschis azi până la {closes} · Sâm 09:00–13:00",
        "emergency_closed": "Acum e închis · deschidem {next}",
        "emergency_112": "Ai dificultăți de respirație sau la înghițire? Sună imediat la 112.",
        "demo_text_disabled": "În modul demo alegi din butoane. Cu ce te pot ajuta?",
        "cancelled_title": "Programare anulată:",
        "cancelled_text": "{date}, {time}, {code}. Locul e din nou liber.",
        "pick_another": "Alege altă oră",
        "pick_another_text": "Vreau să aleg altă oră",
        "ai_unavailable": "Asistentul AI nu e disponibil acum. Te programez pas cu pas, cu butoane.",
        "start_booking": "Începe programarea",
        "rate_limited": (
            "Ai trimis multe mesaje într-un timp scurt. Poți scrie din nou în câteva minute "
            "sau ne poți suna la {phone}."
        ),
        "too_long": "Ai atins limita de {n} de caractere.",
        "empty": "Scrie un mesaj.",
        "refusal": (
            "Nu te pot ajuta cu asta. Îți pot răspunde despre serviciile, prețurile și programul "
            "Clinicii Zâmbet sau te pot programa."
        ),
        "fallback_reply": "Scuze, n-am înțeles. Te pot ajuta cu prețuri, program sau o programare.",
        # booking errors
        "err_unknown_service": "Nu am găsit serviciul. Alege, te rog, din listă.",
        "err_invalid_date": "Nu am înțeles data. Alege, te rog, o zi din listă.",
        "err_invalid_time": "Nu am înțeles ora. Alege, te rog, una din orele libere.",
        "err_misaligned": "Programările încep din sfert în sfert de oră (de ex. 10:00, 10:15).",
        "err_closed_day": "Duminica clinica e închisă. Alege, te rog, altă zi.",
        "err_outside_hours": "În ziua aceea lucrăm {opens}–{closes}; ultima oră de început pentru acest serviciu e {latest}.",
        "err_in_past": "Ora aleasă a trecut deja. Alege, te rog, o oră viitoare.",
        "err_too_soon": "E prea aproape: programările se fac cu cel puțin {lead} de minute înainte.",
        "err_too_far": "Putem face programări cu cel mult {days} de zile înainte.",
        "err_slot_taken": "Ora aleasă tocmai s-a ocupat.",
        "err_invalid_name": "Scrie, te rog, numele și prenumele (doar litere).",
        "err_invalid_phone": "Numărul pare incomplet. Exemplu: 07xx xxx xxx",
        "err_not_found": "Nu am găsit o programare cu acest cod și acest număr de telefon.",
        "err_already_cancelled": "Programarea era deja anulată.",
        "err_already_finished": "Programarea a avut deja loc, nu mai poate fi anulată.",
        "err_too_many_bookings": "Ai atins numărul maxim de programări active din acest chat.",
    },
    "en": {
        "greeting": "Hi! I'm the Clinica Zâmbet assistant. I can help with prices, opening hours or an appointment.",
        "greeting_demo": (
            "Hi! I'm the Clinica Zâmbet assistant. I'm running in demo mode: pick from the buttons "
            "and I'll book you in four steps."
        ),
        "qr_book": "Book an appointment",
        "qr_prices": "Prices",
        "qr_hours": "Hours and address",
        "qr_pain": "I'm in pain",
        "qr_directions": "How do I get there?",
        "qr_thanks": "Thank you!",
        "qr_earliest": "Yes, earliest slot",
        "qr_no_thanks": "No, thanks",
        "qr_new_booking": "Another appointment",
        "ask_service": "Which service would you like to book?",
        "ask_day": "Which day suits you for {phrase}?",
        "days_label": "Pick a day",
        "slots_intro": "On {day} we're open {opens}–{closes}, so for {phrase} ({duration} min) I have:",
        "slots_intro_today": "Today we're open until {closes}, so for {phrase} ({duration} min) I still have:",
        "slots_label": "Free times on {day}",
        "other_day": "Another day",
        "more_times": "Show all times",
        "no_days": (
            "I have no free times for {phrase} in the next 30 days. Please call us on {phone} "
            "and we'll find a solution."
        ),
        "day_full": "That day is fully booked for {phrase}. Please pick another day:",
        "earliest_intro": "The earliest day with room for a consultation is {day}. Free times:",
        "consent": (
            "To book, I need your name and phone number. We use them only for this appointment "
            "and delete them automatically after 24 hours."
        ),
        "consent_form": (
            "By continuing you agree that we use your name and phone only for this appointment. "
            "They are deleted automatically after 24 hours."
        ),
        "consent_link": "How we use your data",
        "name_label": "Full name",
        "name_placeholder": "e.g. Andreea Popescu",
        "phone_label": "Phone",
        "phone_placeholder": "07xx xxx xxx",
        "continue": "Continue",
        "details_sent": "Details sent",
        "summary_intro": "Thank you, {first}! Please check the details before I book:",
        "summary_title": "Appointment summary",
        "row_service": "Service",
        "row_when": "When",
        "row_name": "Name",
        "row_phone": "Phone",
        "row_price": "Price",
        "row_for": "Name",
        "row_address": "Address",
        "row_code": "Code",
        "price_at_clinic": "{price}, paid at the clinic",
        "confirm": "Confirm appointment",
        "modify": "I want to change something",
        "confirmed_label": "You confirmed",
        "superseded_label": "Replaced by a new summary",
        "expired_label": "No longer valid",
        "booked_title": "Appointment confirmed",
        "add_calendar": "Add to calendar",
        "calendar_title": "{service} · {clinic}",
        "calendar_description": "Booking code {code}. Clinic phone {phone}. Demo – fictional clinic.",
        "booked_followup_ai": (
            "See you then! If you can't make it, write to me here and I'll cancel it, or call us on {phone}."
        ),
        "booked_followup_demo": "See you then! If you can't make it, call us on {phone} and we'll cancel it.",
        "booked_note_llm": (
            "[System note: the visitor pressed “Confirm appointment”. Booking confirmed: code {code}, "
            "{service}, {when}, name {name}.]"
        ),
        "modify_ask": "Sure. What would you like to change?",
        "modify_service": "The service",
        "modify_time": "Day or time",
        "modify_contact": "Name or phone",
        "pending_missing": "I can't find the appointment summary any more. Let's start again.",
        "already_confirmed": "That appointment is already confirmed. Your code: {code}.",
        "pending_expired": "The summary expired after 15 minutes. Let's check the time again.",
        "slot_taken": "{time} has just been taken. The closest free times on {day}:",
        "slot_taken_none": "{time} has just been taken and {day} is now full. Please pick another day:",
        "too_many_bookings": "You can hold at most {n} active bookings from this chat. For more, call us on {phone}.",
        "prices_intro": "Our prices (demo values, fictional clinic):",
        "prices_note": "Implants are discussed at the consultation; only the consultation can be booked.",
        "hours_reply": (
            "We're open Monday–Friday 09:00–19:00 and Saturday 09:00–13:00; closed on Sunday. "
            "You'll find us at {address} (fictional address), phone {phone}."
        ),
        "directions": (
            "We're at {address} (a fictional address for this demo). On the page, under "
            "“Program și adresă”, you can open the map."
        ),
        "thanks_reply": "You're welcome! See you soon.",
        "no_thanks_reply": "All right. If you change your mind, I'm here.",
        "pain_reply": (
            "I'm sorry you're in pain. I can't give medical advice, but pain or swelling should be "
            "checked by a dentist soon. Please call the clinic now:"
        ),
        "pain_offer": "Would you also like me to find the earliest free appointment?",
        "emergency_call": "Call {phone}",
        "emergency_open": "Open today until {closes} · Sat 09:00–13:00",
        "emergency_closed": "Closed now · we open {next}",
        "emergency_112": "Trouble breathing or swallowing? Call 112 now.",
        "demo_text_disabled": "In demo mode you choose from the buttons. How can I help?",
        "cancelled_title": "Appointment cancelled:",
        "cancelled_text": "{date}, {time}, {code}. The slot is free again.",
        "pick_another": "Pick another time",
        "pick_another_text": "I'd like to pick another time",
        "ai_unavailable": "The AI assistant isn't available right now. I'll book you step by step, with buttons.",
        "start_booking": "Start booking",
        "rate_limited": (
            "You've sent a lot of messages in a short time. You can write again in a few minutes "
            "or call us on {phone}."
        ),
        "too_long": "You've reached the {n}-character limit.",
        "empty": "Write a message.",
        "refusal": (
            "I can't help with that. I can answer questions about Clinica Zâmbet's services, prices "
            "and opening hours, or book an appointment for you."
        ),
        "fallback_reply": "Sorry, I didn't get that. I can help with prices, opening hours or an appointment.",
        "err_unknown_service": "I couldn't find that service. Please pick one from the list.",
        "err_invalid_date": "I didn't understand the date. Please pick a day from the list.",
        "err_invalid_time": "I didn't understand the time. Please pick one of the free times.",
        "err_misaligned": "Appointments start every 15 minutes (e.g. 10:00, 10:15).",
        "err_closed_day": "The clinic is closed on Sundays. Please pick another day.",
        "err_outside_hours": "That day we're open {opens}–{closes}; the latest start for this service is {latest}.",
        "err_in_past": "That time has already passed. Please pick a future time.",
        "err_too_soon": "That's too soon: appointments must be booked at least {lead} minutes ahead.",
        "err_too_far": "We take bookings at most {days} days ahead.",
        "err_slot_taken": "That time has just been taken.",
        "err_invalid_name": "Please write your first and last name (letters only).",
        "err_invalid_phone": "The number looks incomplete. Example: 07xx xxx xxx",
        "err_not_found": "I couldn't find a booking with that code and phone number.",
        "err_already_cancelled": "That booking was already cancelled.",
        "err_already_finished": "That appointment has already taken place and can't be cancelled.",
        "err_too_many_bookings": "You've reached the maximum number of active bookings from this chat.",
    },
}

# "Ce zi preferi pentru {phrase}?" / "Which day suits you for {phrase}?"
SERVICE_PHRASES: dict[str, dict[str, str]] = {
    "ro": {
        "consultatie": "consultație",
        "detartraj": "detartraj",
        "albire": "albire",
        "plomba": "plombă",
        "extractie": "extracție",
        "implant": "consultația pentru implant",
    },
    "en": {
        "consultatie": "the consultation",
        "detartraj": "the scaling",
        "albire": "the whitening",
        "plomba": "the filling",
        "extractie": "the extraction",
        "implant": "the implant consultation",
    },
}


def t(key: str, lang: str, **values: object) -> str:
    table = TEXTS[norm_lang(lang)]
    text = table.get(key) or TEXTS["ro"][key]
    return text.format(**values) if values else text


def phrase(service_id: str, lang: str) -> str:
    return SERVICE_PHRASES[norm_lang(lang)][service_id]


def error_text(exc: BookingError, lang: str) -> str:
    """Human message for a BookingError."""
    d = exc.details
    values: dict[str, object] = {}
    if exc.code == "outside_hours":
        values = {"opens": fmt_time(d["opens"]), "closes": fmt_time(d["closes"]), "latest": fmt_time(d["latest"])}
    elif exc.code == "too_soon":
        values = {"lead": d.get("lead_minutes", 30)}
    elif exc.code == "too_far":
        values = {"days": d.get("horizon_days", 30)}
    key = f"err_{exc.code}"
    if key not in TEXTS["ro"]:
        key = "err_slot_taken"
    return t(key, lang, **values)
