"""The fictional clinic: services, prices, opening hours and contact details.

This is the single source of truth: the landing page, the AI agent, the demo
flow and the admin calendar all read from here. Prices and durations are
fictional demo values.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, time


@dataclass(frozen=True)
class Service:
    id: str
    name: dict[str, str]  # full name (landing page, LLM)
    label: dict[str, str]  # medium name (chat cards, admin details, legend)
    short: dict[str, str]  # tiny name (calendar blocks)
    description: dict[str, str]
    duration_min: int
    price_ron: int
    aliases: tuple[str, ...]
    color: str  # calendar block background
    accent: str  # calendar block outline / dot
    note: dict[str, str] = field(default_factory=dict)

    def t(self, attr: str, lang: str) -> str:
        values: dict[str, str] = getattr(self, attr)
        return values.get(lang) or values["ro"]


SERVICES: tuple[Service, ...] = (
    Service(
        id="consultatie",
        name={"ro": "Consultație", "en": "Consultation"},
        label={"ro": "Consultație", "en": "Consultation"},
        short={"ro": "Consultație", "en": "Consultation"},
        description={
            "ro": "Examinare completă și un plan de tratament explicat pe înțeles.",
            "en": "A full check-up and a treatment plan explained in plain words.",
        },
        duration_min=30,
        price_ron=150,
        aliases=("consultatie", "consult", "control", "examinare", "consultation", "check-up", "checkup", "exam"),
        color="#DCEFE5",
        accent="#1D5C4F",
    ),
    Service(
        id="detartraj",
        name={"ro": "Detartraj și periaj profesional", "en": "Scaling and professional cleaning"},
        label={"ro": "Detartraj", "en": "Scaling"},
        short={"ro": "Detartraj", "en": "Scaling"},
        description={
            "ro": "Îndepărtăm tartrul și petele, apoi lustruim dinții.",
            "en": "We remove tartar and stains, then polish your teeth.",
        },
        duration_min=45,
        price_ron=250,
        aliases=("detartraj", "periaj", "igienizare", "curatare", "scaling", "cleaning", "hygiene", "tartar"),
        color="#DCEAF1",
        accent="#2F6680",
    ),
    Service(
        id="albire",
        name={"ro": "Albire profesională", "en": "Professional whitening"},
        label={"ro": "Albire", "en": "Whitening"},
        short={"ro": "Albire", "en": "Whitening"},
        description={
            "ro": "Albire în cabinet, într-o singură ședință, cu protecția gingiei.",
            "en": "In-office whitening in a single session, with gum protection.",
        },
        duration_min=90,
        price_ron=1200,
        aliases=("albire", "albirea", "albit", "whitening", "bleaching", "whiten"),
        color="#FBE6D4",
        accent="#B4561E",
    ),
    Service(
        id="plomba",
        name={"ro": "Plombă", "en": "Filling"},
        label={"ro": "Plombă", "en": "Filling"},
        short={"ro": "Plombă", "en": "Filling"},
        description={
            "ro": "Tratăm caria și refacem dintele cu material compozit.",
            "en": "We treat the cavity and restore the tooth with composite.",
        },
        duration_min=60,
        price_ron=300,
        aliases=("plomba", "plombe", "obturatie", "carie", "filling", "cavity"),
        color="#E7E2F4",
        accent="#5B4B9A",
    ),
    Service(
        id="extractie",
        name={"ro": "Extracție", "en": "Extraction"},
        label={"ro": "Extracție", "en": "Extraction"},
        short={"ro": "Extracție", "en": "Extraction"},
        description={
            "ro": "Extracție simplă, cu anestezie locală.",
            "en": "Simple extraction under local anaesthetic.",
        },
        duration_min=45,
        price_ron=350,
        aliases=("extractie", "extractia", "scoatere", "extraction", "extract", "pull"),
        color="#F5DEDD",
        accent="#A23E48",
    ),
    Service(
        id="implant",
        name={"ro": "Consultație pentru implant", "en": "Implant consultation"},
        label={"ro": "Consultație implant", "en": "Implant consultation"},
        short={"ro": "Cons. implant", "en": "Implant cons."},
        description={
            "ro": "Evaluăm situația și îți explicăm opțiunile. Prețul implantului îl afli după consultație.",
            "en": "We assess your situation and explain the options. The implant price is given after the consultation.",
        },
        duration_min=45,
        price_ron=200,
        aliases=("implant", "implantul", "implanturi", "implants", "implant consultation"),
        color="#EEE8D3",
        accent="#7A6522",
        note={
            "ro": "Se programează doar consultația; prețul implantului se stabilește după evaluare.",
            "en": "Only the consultation can be booked; the implant price is set after the assessment.",
        },
    ),
)

SERVICES_BY_ID: dict[str, Service] = {s.id: s for s in SERVICES}

# weekday() -> (opening, closing). Sunday (6) is closed.
OPENING_HOURS: dict[int, tuple[time, time]] = {
    0: (time(9, 0), time(19, 0)),
    1: (time(9, 0), time(19, 0)),
    2: (time(9, 0), time(19, 0)),
    3: (time(9, 0), time(19, 0)),
    4: (time(9, 0), time(19, 0)),
    5: (time(9, 0), time(13, 0)),
}


@dataclass(frozen=True)
class ClinicInfo:
    name: str = "Clinica Dentară Zâmbet"
    short_name: str = "Clinica Zâmbet"
    brand: str = "Zâmbet"
    city: str = "Iași"
    address: str = "Str. Exemplu nr. 12, Iași"
    phone_display: str = "0232 000 000"
    phone_tel: str = "+40232000000"
    emergency_number: str = "112"
    # Piața Unirii, Iași: only used to centre the click-to-load map.
    map_lat: float = 47.1655
    map_lon: float = 27.5823


CLINIC = ClinicInfo()


def strip_accents(text: str) -> str:
    """'Consultație' -> 'consultatie' (also handles the cedilla variants ş ţ)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def find_service(query: str | None) -> Service | None:
    """Resolve a service from an id, a name or a common alias (RO/EN)."""
    if not query:
        return None
    q = strip_accents(query).strip()
    if q in SERVICES_BY_ID:
        return SERVICES_BY_ID[q]
    for service in SERVICES:
        names = {strip_accents(n) for n in (*service.name.values(), *service.label.values())}
        if q in names:
            return service
    # Implant first: "consultatie pentru implant" must not resolve to "consultatie".
    for service in sorted(SERVICES, key=lambda s: s.id != "implant"):
        if any(alias in q for alias in service.aliases):
            return service
    return None


def hours_for(day: date) -> tuple[time, time] | None:
    return OPENING_HOURS.get(day.weekday())
