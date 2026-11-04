"""Romanian/English formatting helpers and a tiny language detector."""

from __future__ import annotations

import re
from datetime import date, datetime, time

from .clinic import strip_accents

WEEKDAYS = {
    "ro": ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
WEEKDAYS_SHORT = {
    "ro": ["Lun", "Mar", "Mie", "Joi", "Vin", "Sâm", "Dum"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
MONTHS = {
    "ro": [
        "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
        "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
    ],
    "en": [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ],
}
MONTHS_SHORT = {
    "ro": ["ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}


def norm_lang(lang: str | None) -> str:
    return "en" if lang == "en" else "ro"


def weekday_name(d: date, lang: str) -> str:
    return WEEKDAYS[norm_lang(lang)][d.weekday()]


def fmt_date(d: date, lang: str, *, year: bool = False, capital: bool = True) -> str:
    """'Joi, 5 noiembrie' / 'Thursday, 5 November'."""
    lang = norm_lang(lang)
    wd = weekday_name(d, lang)
    if capital:
        wd = wd[:1].upper() + wd[1:]
    text = f"{wd}, {d.day} {MONTHS[lang][d.month - 1]}"
    return f"{text} {d.year}" if year else text


def fmt_date_compact(d: date, lang: str) -> str:
    """'joi, 5 nov' / 'Thu, 5 Nov'."""
    lang = norm_lang(lang)
    wd = WEEKDAYS_SHORT[lang][d.weekday()]
    if lang == "ro":
        wd = WEEKDAYS[lang][d.weekday()]
    return f"{wd}, {d.day} {MONTHS_SHORT[lang][d.month - 1]}"


def fmt_day_parts(d: date, lang: str) -> tuple[str, str]:
    """('Joi', '5 nov') for day picker buttons."""
    lang = norm_lang(lang)
    return WEEKDAYS_SHORT[lang][d.weekday()], f"{d.day} {MONTHS_SHORT[lang][d.month - 1]}"


def fmt_time(t: time | datetime) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def fmt_range(start: datetime, end: datetime, sep: str = "–") -> str:
    return f"{fmt_time(start)}{sep}{fmt_time(end)}"


def fmt_int(n: int, lang: str = "ro") -> str:
    """1200 -> '1.200' (RO) / '1,200' (EN)."""
    grouped = f"{int(n):,}"
    return grouped.replace(",", ".") if norm_lang(lang) == "ro" else grouped


def fmt_price(n: int, lang: str) -> str:
    return f"{fmt_int(n, lang)} lei" if norm_lang(lang) == "ro" else f"{fmt_int(n, lang)} RON"


def fmt_week_label(monday: date, saturday: date) -> str:
    """'2 – 7 noiembrie 2026', '30 noiembrie – 5 decembrie 2026', ..."""
    months = MONTHS["ro"]
    if monday.year != saturday.year:
        return (
            f"{monday.day} {months[monday.month - 1]} {monday.year} – "
            f"{saturday.day} {months[saturday.month - 1]} {saturday.year}"
        )
    if monday.month != saturday.month:
        return f"{monday.day} {months[monday.month - 1]} – {saturday.day} {months[saturday.month - 1]} {saturday.year}"
    return f"{monday.day} – {saturday.day} {months[saturday.month - 1]} {saturday.year}"


def plural_bookings_ro(n: int) -> str:
    """Romanian plural: 1 programare, 2 programări, 20 de programări."""
    if n == 1:
        return "1 programare"
    if n == 0 or 1 <= n % 100 <= 19:
        return f"{n} programări"
    return f"{n} de programări"


# --- language detection -----------------------------------------------------

_RO_DIACRITICS = re.compile(r"[ăâîșşțţ]", re.IGNORECASE)
_WORD = re.compile(r"[a-z']+")

_RO_WORDS = {
    "si", "sau", "este", "e", "sunt", "pentru", "aveti", "avem", "vreau", "as", "buna", "salut",
    "multumesc", "mersi", "cat", "costa", "maine", "azi", "astazi", "dinte", "dintii", "dintele",
    "dureri", "durere", "doare", "ziua", "ora", "ore", "va", "rog", "pret", "preturi", "programare",
    "programez", "programa", "unde", "cand", "da", "nu", "mai", "pot", "ce", "la", "de", "cu",
    "din", "pe", "am", "ma", "mi", "imi", "loc", "liber", "libera", "dimineata", "seara",
    "saptamana", "luni", "marti", "miercuri", "joi", "vineri", "sambata", "duminica", "bine",
    "aici", "acum", "vreti", "putea", "puteti", "ati", "sa", "un", "o", "care", "cum",
    "adresa", "program", "anulez", "anuleaza", "albire", "consultatie", "detartraj", "plomba",
}
# Words shared by both languages ("in", "a", "an", "ok") are deliberately left out.
_EN_WORDS = {
    "the", "and", "is", "are", "for", "have", "has", "you", "want", "would", "like", "appointment",
    "book", "booking", "hello", "hi", "hey", "thanks", "thank", "how", "much", "cost", "costs",
    "price", "prices", "tomorrow", "today", "tooth", "teeth", "pain", "when", "where", "what",
    "can", "could", "my", "me", "i", "im", "please", "yes", "no", "slot", "slots", "available",
    "open", "hours", "do", "does", "it", "of", "to", "on", "at", "with", "need",
    "morning", "afternoon", "evening", "week", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "cancel", "whitening", "cleaning", "filling", "extraction",
    "hurts", "swollen", "there", "this", "that", "your", "free", "time", "earliest", "speak",
}


def detect_lang(text: str, fallback: str = "ro") -> str:
    """Best-effort RO/EN guess; keeps `fallback` when the text is ambiguous."""
    if not text:
        return norm_lang(fallback)
    ro = 3 * len(_RO_DIACRITICS.findall(text))
    en = 0
    for word in _WORD.findall(strip_accents(text)):
        if word in _RO_WORDS:
            ro += 1
        if word in _EN_WORDS:
            en += 1
    if ro > en:
        return "ro"
    if en > ro:
        return "en"
    return norm_lang(fallback)
