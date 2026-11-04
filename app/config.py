"""Runtime configuration, read from environment variables.

Every knob has a safe default so `docker compose up` works with an empty
environment: without OPENAI_API_KEY the chat runs in scripted demo mode, and a
missing ADMIN_PASSWORD is replaced by a random one that is printed to the logs.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, replace

log = logging.getLogger("zambet.config")


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        log.warning("Ignoring invalid %s=%r, using %s", name, raw, default)
        return default


def _float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("Ignoring invalid %s=%r", name, raw)
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # LLM
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = ""
    openai_reasoning_effort: str = ""
    openai_temperature: float | None = None
    openai_timeout_seconds: float = 20.0

    # Admin
    admin_password: str = ""
    secret_key: str = ""

    # Storage and time
    database_url: str = "sqlite:///./data/clinic.db"
    timezone: str = "Europe/Bucharest"

    # Abuse and cost protection
    rate_limit_messages: int = 20
    rate_limit_window_seconds: int = 600
    daily_message_cap: int = 500
    daily_token_cap: int = 1_000_000
    max_reply_tokens: int = 350
    max_message_chars: int = 500
    history_messages: int = 20
    max_bookings_per_session: int = 3

    # Demo data lifecycle
    retention_hours: int = 24
    maintenance_interval_minutes: int = 15
    seed_demo_data: bool = True

    # Presentation
    agency_name: str = "Liminova"
    contact_email: str = "contact@exemplu.ro"
    cookie_secure: str = "auto"  # auto | true | false
    api_docs: bool = False
    log_level: str = "info"

    # Set when a secret had to be generated at startup.
    admin_password_generated: bool = False

    @property
    def ai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            openai_api_key=_str("OPENAI_API_KEY"),
            openai_model=_str("OPENAI_MODEL") or cls.openai_model,
            openai_base_url=_str("OPENAI_BASE_URL"),
            openai_reasoning_effort=_str("OPENAI_REASONING_EFFORT"),
            openai_temperature=_float("OPENAI_TEMPERATURE", None),
            openai_timeout_seconds=_float("OPENAI_TIMEOUT_SECONDS", cls.openai_timeout_seconds) or 20.0,
            admin_password=_str("ADMIN_PASSWORD"),
            secret_key=_str("SECRET_KEY"),
            database_url=_str("DATABASE_URL") or cls.database_url,
            timezone=_str("TIMEZONE") or cls.timezone,
            rate_limit_messages=_int("RATE_LIMIT_MESSAGES", cls.rate_limit_messages, 1),
            rate_limit_window_seconds=_int("RATE_LIMIT_WINDOW_SECONDS", cls.rate_limit_window_seconds, 1),
            daily_message_cap=_int("DAILY_MESSAGE_CAP", cls.daily_message_cap),
            daily_token_cap=_int("DAILY_TOKEN_CAP", cls.daily_token_cap),
            max_reply_tokens=_int("MAX_REPLY_TOKENS", cls.max_reply_tokens, 64),
            max_message_chars=_int("MAX_MESSAGE_CHARS", cls.max_message_chars, 20),
            history_messages=_int("HISTORY_MESSAGES", cls.history_messages, 2),
            max_bookings_per_session=_int("MAX_BOOKINGS_PER_SESSION", cls.max_bookings_per_session, 1),
            retention_hours=_int("DATA_RETENTION_HOURS", cls.retention_hours, 1),
            maintenance_interval_minutes=_int("MAINTENANCE_INTERVAL_MINUTES", cls.maintenance_interval_minutes),
            seed_demo_data=_bool("SEED_DEMO_DATA", cls.seed_demo_data),
            agency_name=_str("AGENCY_NAME") or cls.agency_name,
            contact_email=_str("CONTACT_EMAIL") or cls.contact_email,
            cookie_secure=(_str("COOKIE_SECURE") or cls.cookie_secure).lower(),
            api_docs=_bool("API_DOCS", cls.api_docs),
            log_level=(_str("LOG_LEVEL") or cls.log_level).lower(),
        )


def with_generated_secrets(settings: Settings) -> Settings:
    """Fill in secrets that were not provided, logging what was generated."""
    updates: dict[str, object] = {}
    if not settings.admin_password:
        password = secrets.token_urlsafe(12)
        updates["admin_password"] = password
        updates["admin_password_generated"] = True
        log.warning(
            "ADMIN_PASSWORD is not set. Generated a one-off password for this run: %s "
            "(set ADMIN_PASSWORD in .env to keep it stable).",
            password,
        )
    if not settings.secret_key:
        updates["secret_key"] = secrets.token_urlsafe(32)
        log.info("SECRET_KEY is not set; admin sessions will not survive a restart.")
    return replace(settings, **updates) if updates else settings
