"""Public pages: landing page, privacy page, calculator API."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..calculator import DEFAULTS, LIMITS, compute
from ..clinic import CLINIC, OPENING_HOURS, SERVICES
from ..i18n import MONTHS, MONTHS_SHORT, WEEKDAYS, fmt_int, fmt_time
from ..web import templates

router = APIRouter()

_DAY_NAMES = ["Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă", "Duminică"]

CALC_FIELDS = [
    {
        "key": "inquiries", "label": "Cereri pe lună", "unit": "cereri", "step": 10, "inputmode": "numeric",
        "max": LIMITS["inquiries"], "range_max": 1500, "value": DEFAULTS["inquiries"],
        "hint": "Mesaje (WhatsApp, Facebook, site) și apeluri, adunate.",
        "hint_short": "Mesaje și apeluri, adunate.",
    },
    {
        "key": "missedPct", "label": "Rămân fără răspuns sau primesc răspuns prea târziu",
        "label_short": "Fără răspuns sau răspuns târziu", "unit": "%", "step": 1,
        "inputmode": "decimal", "max": LIMITS["missed_pct"], "range_max": 100, "value": DEFAULTS["missed_pct"],
        "hint": "Procent din cereri: apeluri pierdute, mesaje văzute a doua zi.",
        "hint_short": "Apeluri pierdute, mesaje văzute târziu.",
    },
    {
        "key": "conversionPct", "label": "Cereri cu răspuns care devin pacienți", "unit": "%", "step": 1,
        "inputmode": "decimal", "max": LIMITS["conversion_pct"], "range_max": 100, "value": DEFAULTS["conversion_pct"],
        "hint": "Procent: din cei cărora le răspunzi la timp, câți ajung pacienți.",
        "hint_short": "Din cei cărora le răspunzi la timp.",
    },
    {
        "key": "valueRon", "label": "Valoare medie per pacient", "unit": "RON", "step": 50, "inputmode": "numeric",
        "max": LIMITS["value_ron"], "range_max": 10000, "value": DEFAULTS["value_ron"],
        "hint": "În lei: cât încasezi, în medie, de la un pacient nou.",
        "hint_short": "Cât încasezi, în medie, de la un pacient.",
    },
]


def opening_rows() -> list[dict]:
    """Group consecutive days with the same hours: 'Luni – Vineri 09:00 – 19:00'."""
    rows: list[dict] = []
    day = 0
    while day < 7:
        hours = OPENING_HOURS.get(day)
        end = day
        while end + 1 < 7 and OPENING_HOURS.get(end + 1) == hours:
            end += 1
        label = _DAY_NAMES[day] if end == day else f"{_DAY_NAMES[day]} – {_DAY_NAMES[end]}"
        value = f"{fmt_time(hours[0])} – {fmt_time(hours[1])}" if hours else "Închis"
        rows.append({"label": label, "value": value})
        day = end + 1
    return rows


def hero_example(now: datetime) -> dict:
    """The illustrated conversation always talks about a real upcoming weekday."""
    scaling = next(s for s in SERVICES if s.id == "detartraj")
    day = now.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    weekday = WEEKDAYS["ro"][day.weekday()]
    month = MONTHS["ro"][day.month - 1]
    tomorrow = day == now.date() + timedelta(days=1)
    return {
        "ask_day": "mâine" if tomorrow else weekday,
        "answer_day": f"Mâine, {weekday} {day.day} {month}" if tomorrow else f"{weekday.capitalize()}, {day.day} {month}",
        "aria_day": f"{weekday}, {day.day} {month}",
        "toast": f"{weekday.capitalize()}, {day.day} {MONTHS_SHORT['ro'][day.month - 1]} · 11:00 · {scaling.label['ro']}",
        "duration": scaling.duration_min,
        "price": scaling.price_ron,
    }


def map_embed_url() -> str:
    lat, lon = CLINIC.map_lat, CLINIC.map_lon
    bbox = f"{lon - 0.006:.4f},{lat - 0.003:.4f},{lon + 0.006:.4f},{lat + 0.003:.4f}"
    return f"https://www.openstreetmap.org/export/embed.html?bbox={bbox}&layer=mapnik&marker={lat:.4f},{lon:.4f}"


def base_context(request: Request) -> dict:
    settings = request.app.state.settings
    return {"clinic": CLINIC, "agency_name": settings.agency_name, "contact_email": settings.contact_email}


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    now = request.app.state.clock.now_local()
    context = {
        **base_context(request),
        "services": [
            {"id": s.id, "name": s.name["ro"], "description": s.description["ro"],
             "duration_min": s.duration_min, "price": fmt_int(s.price_ron, "ro")}
            for s in SERVICES
        ],
        "hours": opening_rows(),
        "example": hero_example(now),
        "map_src": map_embed_url(),
        "calc": compute(**DEFAULTS),
        "calc_fields": CALC_FIELDS,
    }
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/confidentialitate", response_class=HTMLResponse, include_in_schema=False)
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", base_context(request))


@router.get("/api/calculator", tags=["calculator"])
def calculator(inquiries: str = "200", missed: str = "25", conversion: str = "30", value: str = "800") -> dict:
    """The same formula as the page, for reuse elsewhere (e.g. a quote PDF)."""
    return compute(inquiries, missed, conversion, value).as_dict()
