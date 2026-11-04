"""Cheap, deterministic checks around the LLM.

- `looks_like_injection`: obvious attempts to override the assistant's role are
  answered with a fixed refusal and never reach the model.
- `mentions_emergency`: pain, swelling, bleeding... always get the call card,
  whatever the model says.
- `mask_phones` / `unmask`: phone numbers are swapped for placeholders like
  [TEL-1] before the conversation is sent to the LLM provider, and swapped back
  when a tool runs or a reply is shown.
- `scrub_reply`: blocks replies that leak the prompt canary or the admin
  password, and strips Markdown the widget does not render.
"""

from __future__ import annotations

import re

from .clinic import strip_accents
from .scheduling import BookingError, normalize_phone

_INJECTION = [
    re.compile(p)
    for p in (
        # English
        r"\b(ignore|disregard|override|bypass)\b.{0,40}\b(instructions?|rules|prompt|guidelines|restrictions)\b",
        r"\bforget (all|everything|your|the|any|previous|prior)\b.{0,30}\b(instructions?|rules|prompt)\b",
        r"\b(system|developer|hidden|initial|original)\s+(prompt|message|instructions?)\b",
        r"\b(reveal|show|print|repeat|leak|dump|tell me|give me)\b.{0,30}"
        r"\b(your (prompt|instructions?|rules|guidelines)|the (system )?(prompt|instructions))\b",
        r"\byou are now (a|an|my|the|no longer|free|unrestricted|dan|in \w+ mode)\b",
        r"\b(pretend|act|behave|roleplay)\b.{0,20}\b(as|like|to be)\b.{0,30}"
        r"\b(unrestricted|jailbroken|dan|developer|admin\w*|evil|hacker|different (ai|assistant|bot))\b",
        r"\b(jailbreak\w*|dan mode|developer mode|god mode)\b",
        r"\b(admin\w*|database|db|api)\s+(password|key|token|credentials?)\b",
        r"\bpassword\b.{0,20}\badmin\w*\b",
        r"\bnew instructions?\b",
        # Romanian (accents stripped). Not "uita-te" ("look"), which is common.
        r"\b(ignora|neglija)\w*\b.{0,40}\b(instructiuni\w*|reguli\w*|prompt\w*)\b",
        r"\buita (toate|tot ce|instructiunile|regulile)\b",
        r"\b(arata|spune|dezvaluie|afiseaza|repeta)\w*\b.{0,30}\b(promptul\w*|instructiunile tale|regulile tale)\b",
        r"\bprompt(ul)? (de|al) sistem\w*\b",
        r"\b(esti|vei fi) acum (un|o|in modul|liber)\b",
        r"\bparola\b.{0,20}\badmin\w*\b",
        r"\badmin\w*\b.{0,20}\bparola\b",
        r"\bmod(ul)? (dezvoltator|developer|admin)\b",
        r"\b(instructiuni|reguli) noi\b",
    )
]

_EMERGENCY = [
    re.compile(p)
    for p in (
        # Romanian (accents stripped)
        r"\b(ma|ne|il|o|te) (mai )?doare\b",
        r"\bam (o )?(durere|dureri)\b",
        r"\bdureri\b",
        r"\bdurere (mare|puternica|insuportabila|de dinti|de masea)\b",
        r"\bumfl\w*",
        r"\bsanger\w*",
        r"\babces\w*",
        r"\bpuroi\b",
        r"\bfebr(a|ei|il\w*)\b",  # febră, febril (not "February")
        r"\burgent\w*",
        r"\bnu pot (sa )?(inghit|respir|deschid gura)\w*",
        r"\bdinte\w* (spart|rupt|cazut|scos)\w*",
        r"\b(m-am|s-a) lovit\b",
        r"\baccident\w*",
        # English
        r"\b(my|the|his|her|our) (tooth|teeth|gum|gums|jaw|face|cheek|mouth)\b.{0,30}"
        r"\b(hurts?|hurting|aches?|aching|swollen|swelling|bleeding|bleeds)\b",
        r"\b(it|this|that) (really )?hurts\b",
        r"\b(i'?m|i am) (in|having) (a lot of |so much |severe |bad )?pain\b",
        r"\bi have (a |an )?(lot of |severe |bad )?(pain|toothache|swelling|abscess|fever)\b",
        r"\b(toothache|swollen|swelling|bleeding|abscess|emergency|urgent)\b",
        r"\b(can'?t|cannot) (breathe|swallow|open my mouth)\b",
        r"\b(broken|chipped|knocked[- ]out) tooth\b",
    )
]

_PHONE_CANDIDATE = re.compile(r"(?<![\w+])(?:\+|00)?\d(?:[\s.\-/()]{0,2}\d){8,14}(?!\w)")
_TOKEN = re.compile(r"\[TEL-\d+\]")


def _normalized(text: str) -> str:
    return " ".join(strip_accents(text).split())


def looks_like_injection(text: str) -> bool:
    normalized = _normalized(text)
    return any(p.search(normalized) for p in _INJECTION)


def mentions_emergency(text: str) -> bool:
    normalized = _normalized(text)
    return any(p.search(normalized) for p in _EMERGENCY)


def mask_phones(text: str, pii: dict[str, str]) -> str:
    """Replace valid phone numbers with [TEL-n] placeholders (mutates `pii`)."""

    def replace(match: re.Match) -> str:
        try:
            phone = normalize_phone(match.group(0))
        except BookingError:
            return match.group(0)
        for token, value in pii.items():
            if value == phone:
                return token
        token = f"[TEL-{len(pii) + 1}]"
        pii[token] = phone
        return token

    return _PHONE_CANDIDATE.sub(replace, text)


def unmask(text: str, pii: dict[str, str]) -> str:
    return _TOKEN.sub(lambda m: pii.get(m.group(0), m.group(0)), text)


def resolve_phone(value: str | None, pii: dict[str, str]) -> str:
    """A tool argument may be a placeholder; give back the real number."""
    return unmask(str(value or ""), pii).strip()


_MARKDOWN_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)


def scrub_reply(text: str, *, secrets: list[str], max_chars: int = 1200) -> str | None:
    """Clean a model reply; None if it leaks something it must not."""
    lowered = text.lower()
    if any(s and len(s) >= 4 and s.lower() in lowered for s in secrets):
        return None
    text = _MARKDOWN_BOLD.sub(r"\2", text)
    text = _MARKDOWN_HEADING.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return text
